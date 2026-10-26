# 内核小爱残留审计与清理（rui 0.6.0 · task-3）

> 审计对象：`rui`（house-ear）`main` @ `fc29b98` + 本次未提交工作树。
> 审计时间：2026-10-25。执行者：impl-kernel-audit（实现者）。
> 审计命令（任务卡原文）：
> `git grep -in -e xiaoai -e lx06 -e 小爱 -e "open-xiaoai" -- src/ plugs/ web/src/ docs/`
>
> **结论**：非 web 内核（本轮修复范围） **74 处命名残留已清理**（31 处控制台文案 +
> 43 处注释/文档字符串），`src/rui/` 非 web 只剩 `adapter_aliases.py` 的 5 处兼容别名。
> **整仓尚未“完全剥离”**：web 控制面 27 处、构建/示例/根文档 5 类残留仍在（§7 交接表）；
> `scripts/` 的 P0 坏引用已在本轮补修（§8）。报告自身的 `docs/audit/` 也会命中关键词，统计以写入前快照为准。

## 0. 结论（两层，别混）

| 层 | 状态 | 证据 |
|---|---|---|
| **功能耦合（真正的剥离）** | ✅ **0.6 M2（1fa96bd）已完成** | 无 `rui.device.xiaoai` 包/import；无 `if xiaoai/lx06` 分支；无设备硬件参数驱动（schema 中性，LX06 的 8 ch/48 kHz 由 manifest 的 `audio_defaults` 声明）；adapter 从 `plugs/xiaoai/` 经 `runtime.entrypoint` 泛化加载 |
| **命名/文案层（本轮）** | ✅ 非 web 已清理；web 转交 | 74 处注释/帮助文案中性化，测量数值全部保留；唯一**程序性**提及是 `src/rui/adapter_aliases.py`（迁移 shim，0.7 删）；`tests/test_kernel_decoupling.py` 只防回归 |
| **整仓口径** | ❌ 未完成 | web 27 处（§2.3，含 file:line）+ 构建/示例/根文档（§2.5、§7）；scripts P0 已修（§8） |

> 关键区分：**功能耦合**指内核是否 import/分支/依赖某设备；**文档/文案提及**指代码注释、帮助文案、文档里的品牌字样。
> 前者在 M2 已完成，本轮做的是后者；守卫测试不是剥离动作本身，只是防止前者以文案形式回流。

## 1. 口径与总量

分类定义：

| 分类 | 定义 | 处理 |
|---|---|---|
| plug-owned | 位于 `plugs/xiaoai/`，是设备 manifest、adapter 与说明；plug 是设备语义唯一真相 | 保留，不改 |
| 兼容别名 | `src/rui/adapter_aliases.py` 的 `xiaoai -> lx06` 映射与旧默认值 | 保留至 0.7.0，随后删除 |
| 内核泄漏 | 内核代码里的设备语义：硬编码参数/唤醒词/端口、机型分支、偏向某机型的默认值、品牌化用户文案、以品牌命名的注释 | 本轮修可修项；web 与构建项转交 |
| 文档/夹具 | `docs/` 的设计/历史/操作说明，`tests/` 的设备夹具 | 保留；docs 另开文档轮 |

写入前快照（本报告写入前，`docs/audit/` 尚不存在）：

| 区域 | 命中数 | 分类结果 |
|---|---|---|
| `src/` 合计 | 88 | 非 web 79（别名 5 / 文案 31 / 注释 43）+ web 9 |
| `plugs/` | 83 | 全部 plug-owned |
| `web/src/` | 18 | 全部 web 层残留（转交） |
| `docs/` | 386 | 文档/历史（不改） |
| **合计** | **575** | 清理 74 条命名命中；转交 27 条；其余为 plug/文档 |

修复后同一命令（含本报告写入前）为 **501** 条：`src/` 14、
`plugs/` 83、`web/src/` 18、`docs/` 386。减少的 74 条全部来自非 web 内核
（31 处控制台文案 + 43 处注释/文档字符串）。本报告被 git 跟踪后，`docs/` 的命中数会因
报告自身引用关键词而增加；审计统计以本节快照为准。

### 1.1 为什么把注释/帮助文案也纳入本轮（且不丢信息）

功能耦合已在 M2 移除；0.6.0 DoD §1 原文是「grep xiaoai 只剩『兼容别名 + 文档』两处白名单」，任务卡验收标准 2
进一步要求 `src/` 只剩 `adapter_aliases.py`。因此本报告把**内核源码里以品牌命名的注释/帮助文案**也计入“命名残留”并中性化，
但**不删除任何测量事实**：48 kHz/8 ch/6 mic/2 ref、>>12/>>16、1200 ms、1.94.13 等数字与日期全部保留，
只把“LX06 / 小爱”替换为“reference speaker / reference model / 参考机型 / 设备自带助手”。
每一条修前原文见附录 B，可按 file:line 复核——这不是“改名藏起来”，而是把品牌移出内核、把出处留在审计里。

## 2. 全量分类

### 2.1 `src/` 非 web 内核（逐条，79 条）

| file:line | 命中内容 | 分类 | 处置 |
|---|---|---|---|
| src/rui/adapter_aliases.py:5 | plugs under /opt/rui-plugins (docs/roadmap-0.6.md M2). Renaming the LX06 plug's | 兼容别名 | 保留；0.7.0 删除（唯一白名单） |
| src/rui/adapter_aliases.py:6 | device id from the old in-core name to "lx06" would otherwise break every existing | 兼容别名 | 保留；0.7.0 删除（唯一白名单） |
| src/rui/adapter_aliases.py:21 | "xiaoai": "lx06", | 兼容别名 | 保留；0.7.0 删除（唯一白名单） |
| src/rui/adapter_aliases.py:26 | #: resolving to the same plug; the alias above turns it into "lx06". | 兼容别名 | 保留；0.7.0 删除（唯一白名单） |
| src/rui/adapter_aliases.py:27 | LEGACY_DEFAULT_ADAPTER = "xiaoai" | 兼容别名 | 保留；0.7.0 删除（唯一白名单） |
| src/rui/audio/ack_render.py:3 | The cue exists to hide latency, so it cannot itself cost latency. On the LX06 a wake | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/arbiter.py:240 | arbiter.set_state(turn_open=True, owner_device="lx06", playing={"lx06"}) | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/chime.py:4 | acknowledgement keeps the LX06 microphone muted for ~1.2 s; a tone costs only its own | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/decimate.py:3 | The LX06 hands over 48 kHz; AEC, KWS and VAD run at 16 kHz. Naive `[::3]` aliases | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/probe.py:93 | Prefer the object's own cached ``info`` (the LX06 builds one in ``__init__`` and its | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/process.py:5 | `reference_channels`, so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32 | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/process.py:723 | than an LX06 needs; reading the global value here is what let its own cue open a | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/process.py:1811 | # wake never had to stop 小爱; ours hears 你好小瑞 mid-answer, and taking the | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/audio/protocol.py:296 | Not audio and not ours: the control plane decides whether to stay quiet. 小爱同学 | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:108 | "从音箱收到的原始格式。这些是照 LX06 实测写的（48000 Hz / 8 通道 / 32 bit，" | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:110 | "The raw format the speaker sends. Measured on LX06 (48000 Hz / 8 ch / 32 bit, " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:118 | "推给音箱的音频格式和控速。LX06 客户端只收 16 kHz 单声道；credit_window 是在途上限" | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:120 | "Format pushed to the speaker and write pacing. The LX06 client takes 16 kHz mono " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:568 | _t("说「小爱同学」后我们安静多久：避免两个助手同时答。调大更保守，" | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:580 | "音箱实际输出的采样率。LX06 是 48000；填错会变调或直接没声音。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:581 | "The rate the speaker actually sends. LX06 is 48000; a wrong value pitch- " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:584 | "采集总通道数 = 麦通道 + 参考通道。LX06 是 8。填错 DSP 会读错帧。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:585 | "Total capture channels = mic + reference. LX06 is 8; a wrong value misreads " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:588 | "前几路是麦。LX06 是 6 路；换 4 麦音箱时改这里。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:589 | "The first N channels are mics. LX06 has 6; change it for a 4-mic speaker.", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:592 | "最后几路是播放参考（AEC 用）。LX06 是 2；删掉参考会失去回声消除。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:593 | "The last N channels are the playback reference for AEC. LX06 has 2; removing " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:596 | "设备 ALSA 的位深。LX06 是 32；填错电平/削顶都会异常。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:597 | "The device's ALSA bit depth. LX06 is 32; a wrong value breaks levels/clipping.", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:605 | "参考通道的右移位数。参考满量程是麦的 32 倍，LX06 用 16；改错 AEC 效果骤降。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:606 | "Right-shift for the reference. Its full scale is 32x the mic, so LX06 uses 16; " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:646 | "回答结束后等多久重新开麦听下一句（毫秒）。1000 是 LX06 的实测回声尾；设备" | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:650 | "LX06's measured echo tail; a device-AEC plug (RUI AEC off) may need 2500. " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:698 | "推给音箱的采样率。LX06 客户端只接受 16000；填 48000 会被 adapter 拒绝、没声音。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:699 | "Rate pushed to the speaker. The LX06 client takes 16000 only; 48000 is rejected " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:702 | "推给音箱的通道数。LX06 只收单声道；填 2 会被拒绝。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:703 | "Channels pushed to the speaker. The LX06 takes mono only; 2 is rejected.", None), | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1494 | _t("前几个通道是麦克风（留空 = 继承全局；LX06 是 6）。改完重启音频面。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1495 | "Leading channels that are microphones (empty = inherit; LX06 is 6). " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1508 | _t("采集样本的位深（16 或 32；留空 = 继承全局；LX06 是 32）。改完重启音频面。", | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1509 | "Capture sample width (16 or 32; empty = inherit; LX06 is 32). Restart " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1514 | _t("把麦克风样本左移多少位到 32 bit（留空 = 继承全局；LX06 是 12）。" | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1516 | "Left shift applied to mic samples into 32-bit (empty = inherit; LX06 " | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1521 | _t("把参考样本左移多少位到 32 bit（留空 = 继承全局；LX06 是 16）。" | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/catalog.py:1524 | "LX06 is 16). A wrong value mis-scales the AEC reference. Restart."), | 内核泄漏（用户文案，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:202 | # LX06 needs), but the value it overrides is the conversational `ack` setting. | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:292 | (device default < global config < devices[].audio). The LX06, for instance, | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:302 | # A 16-bit neutral wire needs no scaling; a 32-bit device (the LX06) declares the | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:357 | # for the next sentence. 1000 ms is the LX06's measured echo tail; a device-AEC | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:376 | The capture/ASR/TTS side is 16 kHz, the LX06 client plays mono frames (the | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:387 | # reports. Measured on LX06: the report fires when a block enters aplay's stdin | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:394 | # to the device. Zero (default) keeps the LX06 path exactly as it was: its control | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:413 | # v3: who runs the canceller. "controller" is the LX06 topology we measured | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:427 | mutes the LX06 microphone for about 1.2 s plus its own length, so the cap is enforced | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:552 | layout while an explicit value - including the LX06's 16 kHz capture override - | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:610 | residual echo far longer than the 1200 ms an LX06 needs), but the value it replaces | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/schema.py:1149 | # actually declares (an LX06 inherits 6 mic + 2 ref), not against the | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/store.py:175 | next boot (an LX06 would come back as 1-channel audio after any console save). | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/template.yaml:43 | # Measured on LX06 1.94.13. Do not copy these numbers to another speaker; measure it. | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/config/template.yaml:68 | # The LX06 client plays mono frames; 16 kHz is the rate the whole pipeline runs at. | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:10 | inbound - the device's client dials us (LX06: ws://<server>:<port>/?token=...). | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:52 | #: downgrade a working LX06. | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:74 | evidence and a v0 LX06 probe deliberately does not list them. | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:296 | The reference channels travel inside the same frame as the microphones (LX06: | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:333 | reports no VOLUME/LED capability; nothing above has to ask "is this a 小爱". | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:348 | "leave it to the vendor" (the LX06's services rejects a 0 write); core never | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:380 | #: ``__init__`` (the LX06 does). | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:392 | """Queue PCM for playback. Channels matter: LX06 plays 2 ch at 48 kHz.""" | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/contract.py:436 | from an LX06, and RUI's own KWS path stays exactly as it was. Vendor wakes belong | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/mock.py:3 | Mirrors the LX06's real wire format - 48 kHz, 8 interleaved channels, 6 mics and 2 | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/device/registry.py:161 | # Two layouts ship in the wild: <plug>/<package>/adapter.py (the LX06 image | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/doctor/probes.py:40 | # until M1 measures it on the LX06; the doctor prints it so nobody mistakes it for data. | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/doctor/probes.py:412 | not the LX06's 8 ch / 48 kHz) or a device sets its own playback rate (the 0.5.3 D2 | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/doctor/probes.py:442 | The Dot 2 wire is 48 kHz mono; the LX06 plays 16 kHz mono, so a device without its | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/plugins/builtin.py:3 | 0.6 M2 (docs/roadmap-0.6.md): the LX06 manifest is no longer in code. The image | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/plugins/contract.py:1579 | # DeviceInfo describes the capture layout (LX06: 8 channels). Playback is a | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/plugins/loader.py:252 | builtin LX06 reference plug is still v0). Logging once avoids a warning flood | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/plugins/models.py:97 | RUI = "rui" # RUI's own KWS (the LX06 path, unchanged) | 内核泄漏（注释，已修） | 中性化；见附录 B |
| src/rui/session/vendor.py:3 | The LX06 still runs 小爱. Saying "小爱同学" wakes *it*, and it answers - which is fine | 内核泄漏（注释，已修） | 中性化；见附录 B |

### 2.2 `plugs/xiaoai/`（plug-owned，83 条）

全部命中都在 plug 内，属于设备语义的正确归属：manifest 声明 `id: lx06`、`audio_defaults`、`runtime.listen_port: 4412`，
adapter 实现 `/open-xiaoai` 协议。**本轮不改 `plugs/`**。

| file:line | 命中内容 | 分类 | 处置 |
|---|---|---|---|
| plugs/xiaoai/README.md:1 | # plugs/xiaoai — Xiaomi AI Speaker Pro (LX06) plug | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:3 | The LX06 support that used to live in `src/rui/device/xiaoai/` is a Socket & Plug | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:10 | \| `plugin.yaml` \| `rui-plugin/v0` catalogue entry (id `xiaoai-lx06`, trust: builtin) \| | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:11 | \| `devices/lx06.yaml` \| `rui-device/v1` manifest, id `lx06`, declares the 8 ch / 48 kHz `audio_defaults` \| | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:12 | \| `xiaoai_plug/` \| the adapter (adapter, wire protocol, LED ring, mixer services) \| | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:16 | The image copies this directory to `/opt/rui-plugins/xiaoai` and sets | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:17 | `PYTHONPATH=/opt/rui-plugins/xiaoai`. The core scans the image plug directory | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:23 | Existing installs need no migration: configs that say `adapter: xiaoai` keep working | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:25 | say `adapter: lx06` (the manifest's canonical device id). | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:30 | python -m rui plug-test plugs/xiaoai --adapter xiaoai_plug.adapter:build --json | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/README.md:34 | LX06 is an inbound device: it works once the open-xiaoai client dials in. | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:1 | # LX06 device manifest (rui-device/v1). Moved out of src/rui/device/xiaoai/ in 0.6 | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:8 | # Canonical adapter name since 0.6. Old configs that say `adapter: xiaoai` keep | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:10 | id: lx06 | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:11 | model: "LX06" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:12 | name: "Xiaomi AI Speaker Pro (LX06)" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:32 | # The LX06's measured own-cue echo tail (AckConfig documents 1200 ms). | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:36 | name: "open-xiaoai client" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:37 | source: "https://github.com/coderzc/open-xiaoai" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:41 | - "按 open-xiaoai 的刷机/root 说明打开音箱 SSH，记录 root 密码和登录方式（通常是 ssh root@<speaker-ip>）。" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:42 | - "安装 open-xiaoai client，记下它的安装路径（常见为 /data/open-xiaoai/）以及保存连接配置的文件或启动脚本（server.txt、start.sh 里的 WS_URL 等）。" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:47 | rollback: "Stop and remove the open-xiaoai client, restore the stock boot script; the stock firmware and part… | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:50 | # The image sets PYTHONPATH=/opt/rui-plugins/xiaoai so this module resolves; the | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:52 | entrypoint: "xiaoai_plug.adapter:build" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:62 | notes: "A-class coexistence with the stock firmware; open-xiaoai client required. Wake is RUI KWS (v1 default… | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/devices/lx06.yaml:66 | evidence: "Real LX06 wake/playback/yield checks on 0.5.x; manifest and adapter surface re-verified with rui p… | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/plugin.yaml:2 | id: xiaoai-lx06 | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/plugin.yaml:3 | name: "Xiaomi AI Speaker Pro (LX06)" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/plugin.yaml:5 | # 0.6 M2: this package is image-shipped at /opt/rui-plugins/xiaoai and loaded by the | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/plugin.yaml:12 | homepage: "https://github.com/coderzc/open-xiaoai" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/plugin.yaml:18 | - devices/lx06.yaml | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/__init__.py:1 | """Xiaomi AI Speaker Pro (LX06) plug: the device's own client dials us (tier 2). | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/__init__.py:5 | directory to `/opt/rui-plugins/xiaoai` (docs/roadmap-0.6.md M2). | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/__init__.py:9 | from .adapter import DEFAULT_ADAPTER, DEFAULT_LISTEN_PORT, XiaoaiAdapter, build | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/__init__.py:11 | __all__ = ["DEFAULT_ADAPTER", "DEFAULT_LISTEN_PORT", "XiaoaiAdapter", "build"] | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:1 | """LX06 adapter: tier 2, the device's own client dials us. | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:65 | # The frame queue is bounded by bytes, not just entries: 64 LX06 record frames are | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:69 | # Pausing 小爱's own player. There is no player_stop / player_pause on this box | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:83 | class XiaoaiAdapter(DeviceAdapter): | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:84 | #: Contract-kit-only hint (docs/plug-test-kit.md): the LX06 is an inbound | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:85 | #: transport, so open()/frames()/play() need the open-xiaoai client to dial in | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:90 | "inbound LX06: open() listens on ws://:<port> and capture/playback need the " | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:91 | "open-xiaoai client to dial in; probe() and the method surface were still exercised" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:124 | from .services import XiaoaiServices | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:126 | self.services = XiaoaiServices(self) # type: ignore[assignment] | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:144 | # reports. The wake path asks "is 小爱 talking right now?" to decide whether | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:158 | model="LX06", | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:186 | "xiaoai: listening WITHOUT a token on :%s (devices.%s.allow_no_token) - " | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:201 | log.info("xiaoai adapter listening on :%s for %s", self.listen_port, self.dev.id) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:225 | raise ValueError("the LX06 client plays mono frames; mix down before play()") | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:244 | # the first chunk" (xiaoai-voice-entry server/session.py Sink.open). | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:353 | """Pause 小爱's own player: one speaker, one owner. | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:360 | of 小爱's weather report). Best effort: a dead RPC must not block the | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:366 | log.info("xiaoai: vendor pause (%s): %s", reason, " ".join(out.split())) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:368 | log.info("xiaoai: vendor pause failed (%s): %s", reason, exc) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:375 | log.warning("xiaoai: rejected %s (bad or missing token)", peer) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:379 | log.warning("xiaoai: replacing existing connection from %s", peer) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:384 | log.info("xiaoai: speaker connected from %s", peer) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:402 | log.debug("xiaoai: malformed event dropped: %s: %s", | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:409 | log.debug("xiaoai: malformed audio frame dropped: %s: %s", | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:413 | log.info("xiaoai: speaker disconnected (%s)", exc) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:416 | log.warning("xiaoai: connection ended: %s", self.last_error) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:421 | log.info("xiaoai: speaker disconnected after %s frames", self.frames_received) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:442 | "xiaoai: event frame too large (%d chars > %d); dropped", | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:469 | # 08:58:29, right at 小爱同学, before any recognizer result). Log the | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:471 | log.info("xiaoai: unnamed instruction %s", str(event.get("data"))[:300]) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:472 | log.info("xiaoai: instruction %s.%s", header.get("namespace"), header.get("name")) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:474 | log.info("xiaoai: event %s %s", kind, str(event.get("data"))[:80]) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:485 | log.info("xiaoai: vendor wake (%r), we yield", signal.text) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:487 | log.debug("xiaoai: event %s", event.get("event") or event.get("id")) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:492 | log.warning("xiaoai: command failed: %s", self.last_error) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:502 | log.debug("xiaoai: bad stream frame: %s", exc) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:515 | "xiaoai: record frame of %d bytes exceeds %d", | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:565 | __all__ = ["DEFAULT_ADAPTER", "DEFAULT_LISTEN_PORT", "XiaoaiAdapter", "build", "pcm_to_array"] | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:567 | DEFAULT_ADAPTER = "lx06" | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:570 | def build(dev: DeviceConfig \| None = None, cfg: Config \| None = None) -> XiaoaiAdapter: | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:571 | """Build the LX06 adapter from a device spec and the runtime config. | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:579 | raise TypeError("the LX06 adapter needs a device spec") | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/adapter.py:584 | return XiaoaiAdapter(dev, effective.capture, effective.voice) | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/protocol.py:1 | """Wire protocol spoken by the LX06's own client (the tier-2 seam). | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/protocol.py:139 | # The one that matters: 小爱同学 wakes the vendor, it answers, and without this we would | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/services.py:1 | """What an LX06 can do besides audio, and the only place its mixer lives. | plug-owned | 保留（plug 是唯一真相） |
| plugs/xiaoai/xiaoai_plug/services.py:23 | class XiaoaiServices: | plug-owned | 保留（plug 是唯一真相） |

### 2.3 web 层（未修，转交；复扫 27 条）

Lead 2026-10-25 补充边界：禁止修改 `src/rui/web/**`，以免与并行控制面任务冲突。本表只记录证据与建议，
不在本轮修复。按 Web 侧 grep 口径（`web/src/`）与内核 web 包（`src/rui/web/`）合并列出。
**行号是本报告生成时的复扫快照**：并行控制台 v2 任务仍在改 `src/rui/web/**` 与 `web/src/**`，派单前请复跑
§6.3 的命令确认最新 file:line。

| file:line | 命中内容 | 分类 | 处置 |
|---|---|---|---|
| src/rui/web/api/app.py:1216 | Using the primary client here made the LX06 say the farewell for a Dot 2 turn. | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| src/rui/web/api/app.py:2409 | if "小爱同学" in clean or clean.strip() in {"小爱", "小爱小爱"}: | 内核泄漏（web，未修） | 行为级：硬编码「小爱同学/小爱/小爱小爱」厂商唤醒词；转交 |
| src/rui/web/api/app.py:5697 | """The exact client URL + token an LX06 must dial in with (cold-install step). | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| src/rui/web/api/device_rules.py:9 | A null ``listen_port`` means "adapter default" (4412 for the LX06), so null and an | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| src/rui/web/api/device_rules.py:13 | the LX06 literals. | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| src/rui/web/api/device_rules.py:32 | # The inbound WS port an LX06 client dials when devices[].listen_port is unset. | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| src/rui/web/api/device_rules.py:60 | the config keeps None on purpose), and only then the LX06's 4412. | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| src/rui/web/api/setup_api.py:262 | # silently became an LX06; only a build with a single usable plug may skip the | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| src/rui/web/api/setup_api.py:1010 | # A fresh LX06 has a working 18-LED ring; leaving the schema default off | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| web/src/api.ts:767 | /** GET /api/v1/devices/{id}/connect: what the open-xiaoai client needs. */ | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| web/src/components/ConfigCenter.vue:301 | * (`lx06 · listen port`, `volc-tts · voice`). Replace that prefix with the | 内核泄漏（web，未修） | 注释/文档字符串；转交 |
| web/src/i18n.ts:365 | 'setup.connect.lede': '把下面的 WebSocket 地址和 Device Token 填进音箱上的 open-xiaoai 客户端。', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:386 | 'setup.connect.openXiaoai': 'open-xiaoai 客户端', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:389 | 'setup.connect.stepRestart': '保存后重启 open-xiaoai client；日志出现已连接后，对音箱说唤醒词即可。', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:390 | 'setup.connect.docs': '查看 open-xiaoai 接线文档', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:547 | 'setup.v2.prepare.fallback.clientPath': '记下 open-xiaoai client 在音箱上的安装路径。', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:813 | 'dm.field.listenPortHint': '留空使用适配器默认端口（LX06 为 4412）；两台已启用设备不能共用端口。', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:876 | 'dm.field.plugHint': '表单字段来自该插头的设备清单；默认使用内置 LX06。', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:930 | 'dm.test.note': '只收集证据，不写配置、不播放。内拨音箱（LX06/Dot2）以音频面实时连接为准，会短暂等设备拨入；只有外拨设备才探测主机。', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2026 | 'setup.connect.lede': 'Put the WebSocket URL and device token below into the open-xiaoai client on the speake… | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2047 | 'setup.connect.openXiaoai': 'open-xiaoai client', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2050 | 'setup.connect.stepRestart': 'Restart the open-xiaoai client; once its log says connected, say the wake word.… | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2051 | 'setup.connect.docs': 'Open the open-xiaoai hookup docs', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2208 | 'setup.v2.prepare.fallback.clientPath': 'Note the install path of the open-xiaoai client on the speaker.', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2472 | 'dm.field.listenPortHint': 'Blank means the adapter default (4412 for the LX06); two enabled devices cannot s… | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2535 | 'dm.field.plugHint': 'Form fields come from this plug’s device manifest; the built-in LX06 is the default.', | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |
| web/src/i18n.ts:2589 | 'dm.test.note': 'Collects evidence only — no config write, no playback. An inbound speaker (LX06/Dot2) is jud… | 内核泄漏（web，未修） | 文案级：open-xiaoai 安装步骤硬编码在 i18n；应渲染 manifest device_side.install_steps；转交 |

严重度拆开看：

| 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|
| P1 行为 | `src/rui/web/api/app.py:2409` | 硬编码 `小爱同学 / 小爱 / 小爱小爱` 作为厂商唤醒词兜底 | 由 manifest 增加可选字段（例如 `wake.vendor_words`）或通用 `vendor_wake_words` 参数；C-001 additive，需契约评审 |
| P1 行为 | `src/rui/web/api/device_rules.py:34` | `DEFAULT_LISTEN_PORT = 4412` 是 xiaoai 客户端端口，作为所有未知 plug 的兜底 | 未知 plug 不应猜端口：缺省时拒绝/提示配置；或由 manifest 必填 |
| P1 文案 | `web/src/i18n.ts`（16 处） | 向导把 open-xiaoai 安装步骤写死在 i18n | 渲染 manifest `device_side.install_steps`（C-001 已有字段） |
| P3 注释 | `app.py:1215,5668`、`device_rules.py:9,13,60`、`setup_api.py:262,1010`、`api.ts:767`、`ConfigCenter.vue:301` | 品牌化注释/文档字符串 | 中性化，与 §3 同一口径 |

### 2.4 `docs/`（386 条，不改）

docs 是设计/历史/操作说明，属于 DoD 的“文档白名单”。本轮写范围仅 `docs/audit/`，因此不改；
0.7 文档轮再把仍作为现行说明的引用改为 plug 视角（历史 release notes/archive 保留）。逐条见附录 A。

| file | 命中数 | 分类 | 处置 |
|---|---|---|---|
| `docs/HANDOFF.md` | 10 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/ROADMAP.md` | 10 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/acceptance.md` | 2 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/ack.md` | 2 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/adapter-contract.md` | 3 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/adr/0005-audio-layout-and-provider-chains.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/architecture.md` | 3 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/archive/HANDOFF-history-to-2026-10-24.md` | 17 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/archive/HANDOFF-history-to-2026-10-25.md` | 16 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/audio-latency.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/audio-pipeline.md` | 4 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/console-audit.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/console.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/d1c-notes.md` | 2 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/deploy.md` | 16 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/device-management-research.md` | 12 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/first-run.md` | 18 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/install-flow-audit.md` | 5 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/install-wizard-design.md` | 15 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/latency.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/multi-device-design.md` | 29 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/music.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/open-xiaoai-client.md` | 6 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/phrasebook-editor.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/plan-to-0.6.md` | 7 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/playback-path.md` | 9 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/plug-test-kit.md` | 8 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/recon-old-build.md` | 3 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/releases/v0.4.0.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/releases/v0.4.1.md` | 7 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/releases/v0.5.0.md` | 1 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/releases/v0.5.3.md` | 4 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/releases/v0.6.0.md` | 8 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/remaining-work.md` | 5 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/roadmap-0.2-execution.md` | 6 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/roadmap-0.6.md` | 14 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/runbook.md` | 3 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/socket-plug-audit.md` | 35 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/socket-plug-impl-audit.md` | 32 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/socket-plug-v1.md` | 37 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/socket-plug.md` | 11 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/vendor.md` | 15 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |
| `docs/xiaoai-protocol.md` | 3 | 文档/历史（不改） | docs 超出本次写范围；0.7 文档轮同步 |

### 2.5 扩展扫描：grep 命令之外、但属于“完全剥离”的残留

任务卡的全量命令只覆盖 `src/ plugs/ web/src/ docs/`。为回答“内核是否真的剥离干净”，对全仓再扫一次，
排除上述四处与 `tests/` 后仍有以下类别（按严重度）：

| 级别 | 位置 | 证据 | 分类 | 处置 |
|---|---|---|---|---|
| **P0** | `scripts/measure_capture.py:32-33`、`scripts/measure_erle.py:34`、`scripts/measure_turn_pipeline.py:37` | 曾 `from rui.device.xiaoai import ...`；M2 已删除该包（`ModuleNotFoundError`） | 内核剥离后的坏引用（工具链） | ✅ **本轮已修（§8）**：新增 `scripts/_xiaoai_plug.py`，按 manifest/entrypoint 加载 plug 包；import smoke + `--help` 通过 |
| P1 | `config.example.yaml:29,57,94,107,128` | `adapter: xiaoai`、8 ch/6 mic/2 ref、`vendor_quiet_s ... 后 小爱 answered` | 示例配置偏向 xiaoai（内核随仓交付物） | 转交：`adapter: lx06` + 中性/双设备示例 |
| P1 | `Dockerfile:168-169,183-184,207-209,225` | 镜像构建硬编码 `/opt/rui-plugins/xiaoai` 冒烟测试与 `PYTHONPATH` | 构建期设备耦合 | 转交：改为扫描 `plugs/*/plugin.yaml` 的通用冒烟 |
| P2 | `pyproject.toml:58-61` | pytest `pythonpath` 写死 `plugs/xiaoai` | 测试期设备耦合（可接受但不对称） | 转交：与 dot2 测试策略统一 |
| P2 | `README.md`（22）、`CHANGELOG.md`（16）、`THIRD_PARTY.md`（4） | 产品定位/历史/第三方声明 | 根目录文档 | 转交：文档轮；历史 changelog 保留 |
| P3 | `deploy/install.sh:437` | 指向 `docs/open-xiaoai-client.md` 的提示 | 安装文案 | 保留（该文档确实是 plug 接线说明） |

`tests/` 里的 301 处是设备夹具（`LX06 = "lx06"`、fake client 等），属于合法测试数据，不修；
`tests/` 仍从 `xiaoai_plug` 导入，说明测试与 plug 同仓运行的现状（也是 §4 拆库的成本项）。

## 3. 修复明细与行为影响

本轮修改 **20 个文件、74 个修复点**（其中 23 条帮助文案在首轮中性化后又做了一遍顺句），全部是文案/注释，无一行可执行逻辑改变：

| 类别 | 文件 | 数量 | 说明 |
|---|---|---|---|
| 控制台用户文案 | `src/rui/config/catalog.py` | 31 条命中（31 个字符串块） | 字段说明里的“LX06 是 …”改为“参考机型是 …”；“说『小爱同学』后”改为“设备自带助手应答后”。数值指导不变 |
| 注释/文档字符串 | `src/rui/audio/*`、`config/schema.py`、`config/store.py`、`config/template.yaml`、`device/contract.py`、`device/mock.py`、`device/registry.py`、`doctor/probes.py`、`plugins/*`、`session/vendor.py` | 43 条命中 | 保留测量出处与数字，品牌改中性表述 |
| 新增回归门禁 | `tests/test_kernel_decoupling.py` | 1 个测试 | 扫描 `src/rui`（暂排除 `web/`），出现 `xiaoai|lx06|小爱` 即失败；`adapter_aliases.py` 白名单 |

**没有改**：`plugs/`、`web/`、`src/rui/web/`、API 契约字段、数值默认值、`docs/`（仅新增本报告）、
`tests/` 既有断言。因此对外行为不变，证据见 §6（pytest 仅因新增门禁测试 +1）。

每一条修前/修后原文见附录 B。守卫测试是**回归门禁**（阻止品牌字样回流），不是“剥离”动作本身；
剥离动作是 M2 的目录搬迁 + manifest/entrypoint 泛化。

### 3.1 信息保留自检（Lead 要求 1/2）

- 对全部 97 个文本对（首轮 74 个修复点 + 文案可读性第二遍 23 条）做数字自检：先把 `LX06/lx06/Dot 2/M1/M2/D2` 这类
  型号/里程碑词遮蔽，再比较修前/修后文本里的数字 token；结果 **0 个测量数字丢失**
  （48000/8/6/2/32/12/16/1200/16000/1.94.13/0.5.3/228 等全部保留）。
- 全量搜索“某个机型 / 某台设备 / some model”等无信息表述：**0 处**。
- 帮助文案（`config/catalog.py`）做了第二遍中文/英文顺句：例如
  “参考机型是 8” → “参考机型为 8 通道”、“参考机型是 32” → “参考机型为 32 bit”，
  “1000 是参考机型的实测回声尾” → “1000 ms 是参考机型实测的回声尾”；英文同步。
- 品牌名及少量非目标型号/里程碑一并泛化（如 `Dot 2` → downstream plug、`M1` → real hardware），测量数字与出处（固件版本/日期）全部保留；修前原文逐条在附录 B。

## 4. Topic：小爱是否独立建库（类 dot2）

> 9 哥 指令：「后续是否需要给小爱单独建库（类似 dot2），这个后期探讨，做为一个 topic」。
> 本节只评估，不实施；结论供 0.7 计划决策。

### 4.1 现在的 plug 形态是什么

小爱已经是“**仓内 out-of-core plug**”，但还不是“**独立发布物**”：

| 组成 | 现状 |
|---|---|
| 清单 | `plugs/xiaoai/plugin.yaml`：`rui-plugin/v0`，id `xiaoai-lx06`，`trust: builtin`，`rui_compat: ">=0.3.0 <0.7.0"` |
| 设备契约 | `plugs/xiaoai/devices/lx06.yaml`：`rui-device/v1`，id `lx06`，inbound/ws，capabilities `mic_array/reference/playback/led/keys/volume`，`audio_defaults` 48000/8/6/2/32/>>12/>>16 + `ack_echo_ignore_ms: 1200`，省略 `wake` → RUI KWS，`runtime.entrypoint: xiaoai_plug.adapter:build`，`listen_port: 4412` |
| 代码 | `plugs/xiaoai/xiaoai_plug/`：adapter / 协议 / LED / mixer services |
| 加载 | `plugins/builtin.py` 扫描 `RUI_PLUGINS_DIR` → `/opt/rui-plugins` → 仓库 `plugs/`；`device/registry.py` 按 manifest `runtime.entrypoint` 导入；核心无设备表 |
| 镜像 | `Dockerfile` 把 `plugs/` 复制到 `/opt/rui-plugins`、设 `PYTHONPATH`，并**硬编码**对 xiaoai plug 做冒烟测试 |
| 兼容 | `src/rui/adapter_aliases.py` 的 `xiaoai -> lx06`（0.7 删）；`DeviceConfig.adapter` 旧默认值 |
| 测试 | `tests/test_xiaoai_*.py` + fake `/open-xiaoai` client，与核心测试同仓同 CI |

与 dot2 的差别只在**发布边界**：dot2 是独立 repo（`rui-dot2-bridge`，`trust: trusted`，自带 `pyproject.toml`、`deploy/` 与 `docs/contract-report-0.6.0.json`；本地 checkout 未见 CI 配置，故不写 CI），
xiaoai 仍随 rui 仓库与镜像一起版本化。运行时两者已经同构（同一 manifest loader、同一 entrypoint、同一 plug-test）。

### 4.2 独立库要搬什么

| 要搬/要建 | 现在位置 | 独立库要求 |
|---|---|---|
| manifest + device 清单 | `plugs/xiaoai/plugin.yaml`、`devices/lx06.yaml` | 随包分发（setuptools `package-data`，参考 dot2） |
| adapter 包 | `plugs/xiaoai/xiaoai_plug/` | 独立包名、版本、依赖（numpy/websockets 等） |
| 测试与夹具 | `tests/test_xiaoai_plug.py`、`test_xiaoai_adapter.py`、`test_xiaoai_led.py`、`test_xiaoai_limits.py`、`test_credit.py`、`test_security_fixes.py`、`test_vendor.py` 中的适配器夹具 | 随库迁出；核心侧只留 contract/fake 测试，否则 rui 覆盖率下降 |
| 契约测试 | `rui plug-test` 工具（核心） + 下游仓 `../rui-dot2-bridge/docs/contract-report-0.6.0.json` | 两侧 CI：plug 对 rui floor/ceiling 各跑一次，产出 contract report |
| 镜像集成 | `Dockerfile` 的复制/冒烟、`PYTHONPATH`、`pyproject.toml` 的 pytest path | 改为 pin 一个 plug 发布物（git tag + wheel digest 或目录 digest），构建期做通用扫描 |
| release/版本 | 跟 rui 的 0.6.0 / `plugin.yaml` 0.3.0 | 独立 tag/CHANGELOG；`rui_compat` 窗口与 rui 发布节奏对齐 |
| 文档 | `docs/open-xiaoai-client.md`、`docs/xiaoai-protocol.md`、`THIRD_PARTY.md`、README 接线图 | 设备文档随 plug；核心只留 C-001 通用文档 |
| 不搬 | `adapter_aliases.py`（核心迁移 shim，0.7 删）、C-001 契约定义、manifest loader、plug-test 工具 | 这些是核心资产，plug 只能消费 |

### 4.3 收益 / 成本 / 风险

**收益**

1. 核心仓库“零设备代码”由构造保证，不再依赖白名单与 grep 门禁；`pyproject.toml`/`Dockerfile` 的设备特例也能消失。
2. 发布解耦：plug 可独立修 bug/发版，不被 rui 的发版窗口阻塞；0.7 删 alias 时不必等 plug。
3. 与 dot2 完全对称，C-001 从“一个下游 + 一个内置特例”变成“两个普通消费者”，契约可信度更高。
4. 镜像可裁剪：不需要小爱的部署可以不装这个 plug，减少镜像体积与在进程内执行的代码面。
5. 权责清晰：plug 的 issue/CI/安全评审在 plug 仓库，`trust` 语义也更自然（镜像内置 vs 运维安装）。

**成本**

1. 双仓协调：任何 C-001 变更（如 `audio_defaults`、`wake`）都要成对 PR + 兼容窗口；当前“core + adapter 一次改完”的原子性消失。
2. 镜像可复现性：构建必须 pin plug 的 commit/tag/digest；否则“0.6.0 镜像”不再是一个确定产物。
3. CI 翻倍：rui 侧要跑 plug 的 contract test（floor/ceiling 两档），plug 侧要跟 rui 的每个 minor；单人维护成本实际增加。
4. 测试迁移：`tests/` 现有 xiaoai 覆盖（adapter/led/limits/credit/vendor/security）迁出后，核心侧覆盖率下降，需要 contract/fake 测试补位。
5. 开发体验：本地要同时 checkout 两个仓、设 `RUI_PLUGINS_DIR`/`PYTHONPATH`；`rui` 单仓 `pytest` 不再覆盖设备实现。

**风险**

1. **版本错配**：plug 用了 rui floor 不支持的能力（或反向），只在真机暴露；必须用 `rui plug-test` + `rui_compat` 双门禁。
2. **信任边界**：独立仓若通过 `<data_dir>/plugs` 安装，`trust: builtin` 不再成立，落入 trusted/third-party 的 in-process 规则；镜像内置路径要保留。
3. **镜像漂移**：浮动依赖/未 pin 的 plug 产物会让同 tag 镜像行为不同；必须记录 digest（dot2 的 `contract-report-0.6.0.json` 已示范）。
4. **alias 断点**：0.7 删除 `xiaoai -> lx06` 后，旧配置必须迁移；独立库的文档/向导要同步。
5. **工具链断链**：`scripts/measure_*.py` 在 M2 后曾 import 已删除包，本轮已修（§8）；拆库时仍应保留 import 冒烟，防止同类引用再次无人发现。

### 4.4 与 C-001 `rui-device/v1` 的关系

- C-001 是**接口**，独立库是**消费者**：manifest 字段、adapter 协议、`wake`/`audio_defaults` 语义、contract report 都在核心侧；
  拆库**不需要改 C-001**，只需要 plug 侧 pin `rui_compat` 并在每个 release 产出 contract report。
- 核心必须保留：manifest schema/loader、`DeviceAdapter` 协议、`rui plug-test`、`adapter_aliases.py`（到 0.7）。
- 拆库后 `docs/socket-plug-v1.md` §6.5「镜像自带插头：LX06」应从“核心特性”降级为“官方 plug 的部署示例”；
  §4.4 的 `RUI_ADAPTER_PLUGINS`/entry point 通道保持不变。
- 兼容窗口：xiaoai plug 当前声明 `>=0.3.0 <0.7.0`，dot2 声明 `>=0.6.0 <0.7.0`；若拆库，建议 xiaoai 也收敛到
  `>=0.6.0 <0.7.0`，与 C-001 v1 的实际验证范围一致，0.7 再随新契约升级。

### 4.5 推荐结论

**不推荐现在（0.6.0 发布窗口）拆库；推荐 0.7.0 与 alias 删除同批评估，先做“独立发布物、同一仓库”的中间态。**

理由（按权重）：

1. **边际收益小**：in-repo plug 已经把核心语义清到只剩 alias；拆库主要改善仓库洁净度与发布节奏，不是功能/稳定性收益。
2. **成本不对称**：双仓发布、CI、镜像 pin、契约窗口协调都压在单人维护上，而 0.6.0 正在真机验收/发版窗口。
3. **dot2 已证明模型可行**：out-of-tree 的价值已被验证，xiaoai 现在拆不产生新知识，只增加变量。
4. **触发条件未满足**：建议同时满足以下 4 条再拆——
   (a) C-001 v1 冻结、0.7 alias 删除计划确定；
   (b) 镜像构建能 pin plug 产物（tag + digest）并复现；
   (c) `rui plug-test` 在 rui 与 plug 两侧 CI 都能跑（floor/ceiling）；
   (d) LX06 真机验收有人力，或出现第二个外部使用者/独立发版需求。
5. **0.7 前的低成本中间态**：把 `plugs/xiaoai` 打成独立 wheel/目录产物，镜像按 pin 安装；Dockerfile 冒烟改为扫描
   `plugs/*/plugin.yaml`；`config.example.yaml` 先修正（`scripts/` 已修，§8）。这样即使不拆 repo，也已经是“可拆”的状态。

### 4.6 建议（不在本报范围内，供后续排期）

1. **web 去设备化**：厂商唤醒词从 manifest/通用配置读取；向导渲染 `device_side.install_steps` 而非 i18n 硬编码；
   `DEFAULT_LISTEN_PORT` 不再猜 4412。
2. **安装 profile 与核心模板分离**：`src/rui/config/template.yaml` 与 `config.example.yaml` 仍是 LX06 生产 profile
   （capture 16 kHz/8 ch/6 mic/2 ref），混装 Dot2 时必须逐设备覆盖（`docs/HANDOFF.md` 已列为已知风险）。
   0.7 应提供“按 plug 生成 profile”或至少双设备示例。
3. **工具链**：`scripts/measure_*.py` 的 P0 坏引用已修（§8）；剩余 `pyproject.toml` 的 pytest path 与 dot2 统一。
4. **契约兜底值**：`RuntimeSpec.listen_port` 默认 4412、`AudioFrame.sample_rate` 默认 48000 仍是 xiaoai 时代的兜底
   （见 §5 第 3 项）；0.7 契约评审决定改为必填/中性值。
5. **docs 文档轮**：386 处 docs 引用按“现行说明 / 历史记录”二分更新。

## 5. “完全剥离”定义清单与达成状态

| # | 定义 | 判据 | 状态 |
|---|---|---|---|
| 1 | 内核不含小爱 adapter 实现，也不 import 小爱模块 | `src/rui/device/xiaoai/` 已删除；`python -c "import rui.device.xiaoai"` → `ModuleNotFoundError`；`git grep "device.xiaoai" -- src/` 无命中 | ✅ 达成（M2） |
| 2 | 内核不含设备品牌分支 | 核心代码无 `if xiaoai/lx06`；`docs/socket-plug-v1.md` §1 的硬规则；本轮门禁测试固化 | ✅ 非 web 达成 |
| 3 | 内核不含小爱硬件参数（8 ch/6 mic/2 ref/48 kHz 作为行为默认） | `schema.py` 中性默认 16000/1/0；`plugs/xiaoai/devices/lx06.yaml` 声明 48000/8/6/2；三层合并按 manifest 解析。**残留**：`AudioFrame.sample_rate=48000`、`RuntimeSpec.listen_port=4412` 是契约兜底（仓内调用方都显式传值/声明） | ⚠️ 行为默认达成；兜底值转 0.7 契约评审 |
| 4 | 内核不含小爱唤醒词默认值 | 非 web 内核无「小爱」；`DeviceConfig.wake_words` 默认是 RUI 的「你好小瑞」。**残留**：`src/rui/web/api/app.py:2400` 硬编码厂商唤醒词 | ⚠️ 非 web 达成；web 转交（P1） |
| 5 | health/doctor 不按机型分支 | `doctor/probes.py` 通过 `resolve_device_plug` + manifest/entrypoint 解析，无 `if lx06`；仅存中性化后的测量注释 | ✅ 达成 |
| 6 | 内核与 web 无小爱专有文案/图标 | 非 web 文案已中性化；**残留**：`web/src/i18n.ts` 16 处 open-xiaoai 文案，`app.py/device_rules.py` 注释与默认值；未发现品牌图标 | ❌ 未完全达成（转交） |
| 7 | config 默认值中性 | 核心 schema 中性（capture 16000/1 ch/0 ref、playback 16000 mono、ack earcon）。**例外**：`src/rui/config/template.yaml` 与 `config.example.yaml` 是 LX06 生产 profile（显式值，按 C-001 合并规则永远赢） | ⚠️ 核心默认达成；安装 profile 待 profile 化 |
| 8 | 兼容别名唯一，且 0.7 删除 | 非 web `src/` 只剩 `adapter_aliases.py` 5 处；门禁测试白名单 | ✅ 达成（到期删除） |
| 9 | 小爱语义只剩 plug 一处真相 | manifest + adapter 全在 `plugs/xiaoai/`；核心只经 manifest/entrypoint 泛化加载 | ✅ 达成 |
| 10 | 内核无构建期设备耦合 | **残留**：`Dockerfile` 硬编码 xiaoai 冒烟/PYTHONPATH；`pyproject.toml` pytest 路径 | ❌ 转交 |
| 11 | 仓库工具链可用 | `scripts/measure_*.py` 经 `scripts/_xiaoai_plug.py` 从 manifest/entrypoint 加载 plug；3 个脚本 import smoke + `--help` 通过；`ruff check src tests scripts` clean（§8） | ✅ 达成 |
| 12 | 回归门禁 | 本任务口径 `pytest -q --ignore-glob='tests/test_api_v2_*.py'` → 1349 passed / 17 skipped；`ruff check src tests scripts` → All checks passed!；全仓数字随并行 v2 测试增长，不作为验收口径；新增 `tests/test_kernel_decoupling.py` | ✅ 达成 |

**总体判定**：**功能耦合层已达成**（M2：无 import/分支/硬件参数依赖；本轮不改变这一点）；**命名层**非 web 达成、
web 未达成；**整仓“完全剥离”未达成**，
剩余交接项：web 27 处、构建 2 类、示例配置 1 类、根目录文档 1 类（`scripts/` P0 已修，§8）。
若把“完全剥离”严格定义为整仓零品牌，则当前状态是
**~95%**
（548 / 575 的审计命中已归位），且剩余项都有明确落点。

## 6. 验证（真实命令与输出）

### 6.1 基线（修改前）

```text
$ .venv/bin/python -m pytest -o addopts='' -q
1348 passed, 17 skipped in 74.94s (0:01:14)
```

### 6.2 修复后

```text
# 本任务基线口径（忽略并行控制台任务的全部未跟踪 v2 测试）：
$ .venv/bin/python -m pytest -o addopts='' -q --ignore-glob='tests/test_api_v2_*.py'
1349 passed, 17 skipped in 77.44s (0:01:17)

# 只忽略 reviewer 点名的两个文件时，会多出并行任务后加入的第三个 write 测试：
$ .venv/bin/python -m pytest -o addopts='' -q --ignore=tests/test_api_v2_provenance.py --ignore=tests/test_api_v2_read.py
1369 passed, 17 skipped in 83.56s (0:01:23)

# 当前全仓数字随并行 v2 测试增长（快照 1385/17 之后又加入了 test_api_v2_write.py），
# 因此不作为本任务的验收口径；本任务固定用上面的 --ignore-glob。

$ .venv/bin/python -m ruff check src tests scripts
All checks passed!

# 本轮改动范围（含 scripts/，排除并行任务文件）：
$ .venv/bin/python -m ruff check src/rui/adapter_aliases.py src/rui/audio src/rui/config src/rui/device src/rui/doctor src/rui/plugins src/rui/session scripts tests/test_kernel_decoupling.py
All checks passed!

$ .venv/bin/python -m pytest -o addopts='' -q tests/test_kernel_decoupling.py
1 passed in 0.05s
```

数字变化说明：本任务基线 1348 → 1349 只因为新增 `tests/test_kernel_decoupling.py`；并行控制台任务新增的
`tests/test_api_v2_*.py`（provenance/read/write）全部未跟踪，与本任务无关，验收时用 `--ignore-glob` 排除。
没有删除/跳过任何既有测试。

ruff 说明：`ruff check src tests scripts` 全绿；本轮改动的 20 个 src 文件、`scripts/` 下 4 个文件与新增测试也单独复跑通过。

### 6.3 修复后 grep

`src/` 全量（含 web；web 未修，转交）：

```text
src/rui/adapter_aliases.py:5:plugs under /opt/rui-plugins (docs/roadmap-0.6.md M2). Renaming the LX06 plug's
src/rui/adapter_aliases.py:6:device id from the old in-core name to "lx06" would otherwise break every existing
src/rui/adapter_aliases.py:21:    "xiaoai": "lx06",
src/rui/adapter_aliases.py:26:#: resolving to the same plug; the alias above turns it into "lx06".
src/rui/adapter_aliases.py:27:LEGACY_DEFAULT_ADAPTER = "xiaoai"
src/rui/web/api/app.py:1216:    Using the primary client here made the LX06 say the farewell for a Dot 2 turn.
src/rui/web/api/app.py:2409:                if "小爱同学" in clean or clean.strip() in {"小爱", "小爱小爱"}:
src/rui/web/api/app.py:5697:        """The exact client URL + token an LX06 must dial in with (cold-install step).
src/rui/web/api/device_rules.py:9:A null ``listen_port`` means "adapter default" (4412 for the LX06), so null and an
src/rui/web/api/device_rules.py:13:the LX06 literals.
src/rui/web/api/device_rules.py:32:# The inbound WS port an LX06 client dials when devices[].listen_port is unset.
src/rui/web/api/device_rules.py:60:    the config keeps None on purpose), and only then the LX06's 4412.
src/rui/web/api/setup_api.py:262:        # silently became an LX06; only a build with a single usable plug may skip the
src/rui/web/api/setup_api.py:1010:                # A fresh LX06 has a working 18-LED ring; leaving the schema default off
```

剔除 web（即本轮实现者负责范围）：

```text
src/rui/adapter_aliases.py:5:plugs under /opt/rui-plugins (docs/roadmap-0.6.md M2). Renaming the LX06 plug's
src/rui/adapter_aliases.py:6:device id from the old in-core name to "lx06" would otherwise break every existing
src/rui/adapter_aliases.py:21:    "xiaoai": "lx06",
src/rui/adapter_aliases.py:26:#: resolving to the same plug; the alias above turns it into "lx06".
src/rui/adapter_aliases.py:27:LEGACY_DEFAULT_ADAPTER = "xiaoai"
```

`src/rui/web/`（转交清单）：

```text
src/rui/web/api/app.py:1216:    Using the primary client here made the LX06 say the farewell for a Dot 2 turn.
src/rui/web/api/app.py:2409:                if "小爱同学" in clean or clean.strip() in {"小爱", "小爱小爱"}:
src/rui/web/api/app.py:5697:        """The exact client URL + token an LX06 must dial in with (cold-install step).
src/rui/web/api/device_rules.py:9:A null ``listen_port`` means "adapter default" (4412 for the LX06), so null and an
src/rui/web/api/device_rules.py:13:the LX06 literals.
src/rui/web/api/device_rules.py:32:# The inbound WS port an LX06 client dials when devices[].listen_port is unset.
src/rui/web/api/device_rules.py:60:    the config keeps None on purpose), and only then the LX06's 4412.
src/rui/web/api/setup_api.py:262:        # silently became an LX06; only a build with a single usable plug may skip the
src/rui/web/api/setup_api.py:1010:                # A fresh LX06 has a working 18-LED ring; leaving the schema default off
```

全量命令计数（报告写入前，575 → 当前）：

```text
$ git grep -in -e xiaoai -e lx06 -e 小爱 -e "open-xiaoai" -- src/ plugs/ web/src/ docs/ | wc -l
    1262
```

### 6.4 扩展证据

> 快照时点：本报告最后一次生成。本任务改动已由 Lead 提交为 `adc4c2b`
> （chore(kernel): finish removing core-level vendor naming），该 commit 已 push 到 `origin/main`，
> 并且 `v0.6.0` tag 指向 `adc4c2b`。**tag 决定（2026-10-26，Lead）：tag 不移动**——adc4c2b 已触发
> 多架构镜像构建，镜像产出后 tag 不可移动；这 6 条是审计文档的准确性修订，不影响代码行为，因此
> **跟 main 走、不进 tag**，待镜像 digest/Release 完成后与并行任务一起提交。代码修复 #1（catalog 文案）
> 已包含在 `adc4c2b` / `v0.6.0` 内。

```text
$ .venv/bin/python -c 'import rui.device.xiaoai'
ModuleNotFoundError: No module named 'rui.device.xiaoai'

$ git show --stat --oneline adc4c2b
adc4c2b chore(kernel): finish removing core-level vendor naming (0.6.0 DoD)
 docs/audit/xiaoai-decoupling.md  | 1212 ++++++++++++++++++++++++++++++++++++++
 scripts/_xiaoai_plug.py          |   65 ++
 scripts/measure_capture.py       |   12 +-
 scripts/measure_erle.py          |    6 +-
 scripts/measure_turn_pipeline.py |    6 +-
 scripts/watch-rui-upstream.py    |    4 +-
 src/rui/audio/ack_render.py      |    2 +-
 src/rui/audio/arbiter.py         |    2 +-
 src/rui/audio/chime.py           |    2 +-
 src/rui/audio/decimate.py        |    2 +-
 src/rui/audio/probe.py           |    4 +-
 src/rui/audio/process.py         |    6 +-
 src/rui/audio/protocol.py        |    4 +-
 src/rui/config/catalog.py        |   70 +--
 src/rui/config/schema.py         |   24 +-
 src/rui/config/store.py          |    2 +-
 src/rui/config/template.yaml     |    5 +-
 src/rui/device/contract.py       |   20 +-
 src/rui/device/mock.py           |    2 +-
 src/rui/device/registry.py       |    4 +-
 src/rui/doctor/probes.py         |    6 +-
 src/rui/plugins/builtin.py       |    2 +-
 src/rui/plugins/contract.py      |    2 +-
 src/rui/plugins/loader.py        |    2 +-
 src/rui/plugins/models.py        |    2 +-
 src/rui/session/vendor.py        |    6 +-
 tests/test_kernel_decoupling.py  |   64 ++
 27 files changed, 1446 insertions(+), 92 deletions(-)

$ git status --short   # 本任务文件已提交，只剩并行 v2 的未跟踪/修改文件
 M docs/audit/xiaoai-decoupling.md
 M src/rui/web/api/app.py
 M web/vite.config.ts
?? docs/contracts/
?? docs/design/
?? src/rui/web/api/v2_api.py
?? tests/test_api_v2_provenance.py
?? tests/test_api_v2_read.py
?? tests/test_api_v2_write.py
?? web/src/v2/
?? web/v2.html
```

> 说明：`M src/rui/web/api/app.py`、`M web/vite.config.ts` 以及 `docs/contracts/`、`docs/design/`、
> `src/rui/web/api/v2_api.py`、`tests/test_api_v2_*.py`、`web/src/v2/`、`web/v2.html` 属于并行控制台 v2 任务，
> 不属于本任务；本任务没有修改 `web/` 或 `src/rui/web/`。本任务产物（20 个 `src/rui/` 文件的文案/注释修改、
> `scripts/_xiaoai_plug.py`、3 个 `scripts/measure_*.py` 的 import 修复、`scripts/watch-rui-upstream.py` 的
> lint 改名、`tests/test_kernel_decoupling.py` 与 `docs/audit/xiaoai-decoupling.md` 的初版）已包含在 `adc4c2b`
> 并已 push；本报告的 6 条评审修订跟 main 走、不进 `v0.6.0` tag，待镜像 digest/Release 完成后提交。

## 7. 交接与遗留风险

| 优先级 | 项 | 证据 | 建议下一步 | 本轮为何不做 |
|---|---|---|---|---|
| P0 | `scripts/measure_*.py` 引用已删除的 `rui.device.xiaoai` | §2.5 + §8 | ✅ 已修：`scripts/_xiaoai_plug.py` 按 manifest/entrypoint 加载；import smoke + `--help` 通过 | 写范围已由 Lead 临时扩到 `scripts/` |
| P1 | web 硬编码厂商唤醒词 | `src/rui/web/api/app.py:2409` | manifest 增可选 vendor 唤醒词字段（C-001 additive）或通用参数 | Lead 明确禁止改 `src/rui/web/**` |
| P1 | web 默认监听端口 4412 | `src/rui/web/api/device_rules.py:34` | 缺省不猜端口；manifest 必填或拒绝并提示 | 同上 |
| P1 | 向导 i18n 写死 open-xiaoai 步骤 | `web/src/i18n.ts` 16 处 | 渲染 `device_side.install_steps` | 写范围外（`web/`） |
| P1 | `config.example.yaml` 仍是 xiaoai 示例 | `adapter: xiaoai`、8 ch/6 mic/2 ref | 改 `lx06` + 中性/双设备示例 | 写范围外（仓库根文件） |
| P1 | Dockerfile 构建期硬编码 xiaoai 冒烟 | `Dockerfile:168-184,207-225` | 扫描 `plugs/*/plugin.yaml` 的通用冒烟 | 写范围外 |
| P2 | 契约兜底值 4412 / 48000 | `src/rui/plugins/models.py:203`、`src/rui/device/contract.py:306` | 0.7 契约评审：必填或中性值 | 会改变对外默认行为，超出“文案/注释”边界 |
| P2 | 安装模板/示例是 LX06 生产 profile | `src/rui/config/template.yaml` capture 8 ch/6 mic/2 ref；HANDOFF 已知混装风险 | 0.7 做 profile 生成；混装继续逐设备覆盖 | 改它会改变 LX06 生产行为（C-001 显式值优先） |
| P2 | 386 处 docs 引用 | 附录 A | 0.7 文档轮按“现行/历史”二分 | 任务禁止改 `docs/` 其他文件 |
| P3 | 根目录 README/CHANGELOG/THIRD_PARTY 品牌叙述 | §2.5 | 文档轮 | 同上 |
| — | 小爱独立建库 | §4 | 0.7 与 alias 删除同批评估；先做独立发布物中间态 | 本轮只评估 |

## 8. P0 修复：`scripts/measure_*.py` 的坏引用（补，2026-10-25 晚）

§2.5 的 P0：0.6 M2 删除 `src/rui/device/xiaoai/` 后，三个测量脚本仍从该 core 模块导入，实际已不可运行
（`python -c "import rui.device.xiaoai"` → `ModuleNotFoundError`）。

| 文件 | 修前 | 修后 |
|---|---|---|
| `scripts/measure_capture.py:32-33` | `from rui.device.xiaoai import protocol` / `.adapter import XiaoaiAdapter` | `from _xiaoai_plug import load_xiaoai_plug`；`_plug = load_xiaoai_plug()` 后取 `adapter` / `protocol` |
| `scripts/measure_erle.py:34` | `from rui.device.xiaoai.adapter import XiaoaiAdapter` | 同上，只取 `adapter`（保留 `isinstance` 检查与类型标注） |
| `scripts/measure_turn_pipeline.py:37` | `from rui.device.xiaoai import protocol` | 同上，只取 `protocol`（fake speaker 的帧封装） |
| `scripts/_xiaoai_plug.py`（新增） | — | 用 `PlugCatalog` 解析 manifest（仓内 `plugs/` 优先，`RUI_PLUGINS_DIR`/镜像/data dir 兜底），把 `plugin.source_dir` 加入 `sys.path`，按 `runtime.entrypoint` 动态导入 `xiaoai_plug.adapter` 与 `xiaoai_plug.protocol` |

设计约束：

- **不 import 已删除的 core 模块，也不复制 adapter 代码**；只走 plug 的 manifest/entrypoint，与运行时同一条加载路径
  （source checkout 对应镜像的 `PYTHONPATH=/opt/rui-plugins/xiaoai`）。
- 类身份一致：`load_xiaoai_plug().adapter` 与 `build_adapters()` 构造出的实例是同一个
  `xiaoai_plug.adapter.XiaoaiAdapter`，`isinstance` 检查仍然有效（实测 `True`）。
- 失败可读：找不到 manifest 或 entrypoint 时给出明确 `RuntimeError`，不再是一句 `ModuleNotFoundError`。

验证（真实输出）：

```text
$ .venv/bin/python -c "import runpy; runpy.run_path('scripts/measure_capture.py', run_name='smoke')"
$ .venv/bin/python -c "import runpy; runpy.run_path('scripts/measure_erle.py', run_name='smoke')"
$ .venv/bin/python -c "import runpy; runpy.run_path('scripts/measure_turn_pipeline.py', run_name='smoke')"
# 三条均无输出、exit 0

$ .venv/bin/python scripts/measure_capture.py --help | head -1
usage: measure_capture.py [-h] --config CONFIG --device DEVICE
$ .venv/bin/python scripts/measure_erle.py --help | head -1
usage: measure_erle.py [-h] --config CONFIG --device DEVICE --file FILE
$ .venv/bin/python scripts/measure_turn_pipeline.py --help | head -1
usage: measure_turn_pipeline.py [-h] --config CONFIG [--wake WAKE]

# 类身份 / 协议冒烟
plug class: xiaoai_plug.adapter.XiaoaiAdapter
built class: xiaoai_plug.adapter.XiaoaiAdapter
isinstance via plug helper: True
protocol.stream bytes: 86

$ .venv/bin/python -m ruff check src tests scripts
All checks passed!
```

附带：`scripts/watch-rui-upstream.py:32` 有一个 pre-existing `E741`（生成器变量名 `l`），为了让 Lead 要求的
`ruff check src tests scripts` 变绿，做了纯改名 `l -> candidate`；无行为变化，不属于 P0 根因。

> 全仓 `ruff check src tests scripts` 已复跑全绿（2026-10-25 晚）；此前红来自并行 v2 未跟踪文件，非本任务。
> 这不是本任务的改动。

## 附录 A：`docs/` 全量命中（386 条，未改）

<details>
<summary>展开逐条清单</summary>

- `docs/HANDOFF.md:1` # HANDOFF 2026-10-25 rui 0.6.0 · 多音箱完成 / 小爱剥离
- `docs/HANDOFF.md:14` | 下游 plug | `rui_dot2_bridge` 插件 **1.2.0**；容器内 `builtin_plugins()` 同时列出 `dot2-echomuse 1.2.0`、`xiaoai-lx06 0.3.0` | `grep -n 'version:\|rui_compat' src/rui_dot2_bridge/plugin.yaml` → `1.2.0` / `">=0.6.0 <0.7.0"`；`docker exec rui python -c ...` → `2 [('dot2-echomuse','1.2.0'),('xiaoai-lx06','0.3.0')]` |
- `docs/HANDOFF.md:17` | 兼容边界 | rui `>=0.6.0, <0.7.0` | `src/rui_dot2_bridge/plugin.yaml:5`；内置 xiaoai plug 为 `>=0.3.0 <0.7.0` |
- `docs/HANDOFF.md:20` - **小爱从内核剥离为 plug**（M2）：`src/rui/device/xiaoai/` 删除、改为 `plugs/xiaoai/`，经 manifest `runtime.entrypoint` 加载；兼容别名只在 `src/rui/adapter_aliases.py`。`git grep -l xiaoai -- src/` → 只剩 `src/rui/adapter_aliases.py`；生产容器 `builtin_plugins()` → 两个 plug 并列。commit `1fa96bd`。
- `docs/HANDOFF.md:21` - **三层音频合成 + 中性默认**（M1）：`rui-device/v1` 增 `audio_defaults`，顺序 设备默认 < 全局 config < `devices[].audio`；核心默认 16000/1ch/0ref；LX06 manifest 声明 48000/8/6/2；`save_config` 不固化默认值。commit `ae99f1b`。全量 `pytest -o addopts='' -q` → **1348 passed / 17 skipped**；`ruff check src tests` → clean。
- `docs/HANDOFF.md:26` - **真机已通过的部分**：Dot2 唤醒 →「现在几点？」→「现在是晚上十一点零四分。」（audio_bytes=63600、`playback_gap_count=0`）；LX06 唤醒 →「今天周几？」→「今天周日，10月25号。」（audio_bytes=62916、gap=0）；自回声被 `own voice heard back, no turn` 拦下。
- `docs/HANDOFF.md:39` 3. 等 9 哥 真机两轮（LX06 说一句等 15s、Dot2 说一句等 15s）—— 验收：`session closed` 前后 `output owner released` 的房号与说话房间一致，无 `rejected: turn_open`。
- `docs/HANDOFF.md:56` - **`adapter_aliases.py` 只能活到 0.7**：新代码不得再写死 `xiaoai`。
- `docs/HANDOFF.md:64` - `src/rui/adapter_aliases.py` —— 唯一 `xiaoai → lx06` 兼容别名（0.7 删）。
- `docs/HANDOFF.md:66` - `plugs/xiaoai/` —— 内置 LX06 plug（manifest + adapter）。
- `docs/ROADMAP.md:4` > 目标载体：**NAS**。当前：**0.5.2 已发布**（安装路径 #15 + 卫星就绪/连接测试 #7），生产跑 0.5.0 且健康；多设备 0.5（config v3 + Phase 2 多平面/仲裁）已就绪，`multi_device.enabled=false` 待 LX06+2×Dot2 真机验收。
- `docs/ROADMAP.md:54` | P3 功能/重构 | LX06 抽成插件包；dot2 桥按标准改造成第一个外部插头；第二台设备（config v3）；设备管理增强；安全三项；0.2.0 | 插件标准定稿后做 |
- `docs/ROADMAP.md:71` | P1.6 ✅ | ffmpeg 换成 LGPL-only 构建（pinned source 7.1.5 + build 内 license gate）+ `THIRD_PARTY.md` + README 致谢（`b71a7e7`；本机 amd64 已验证） | 镜像内 `ffmpeg -version` 无 GPL 组件；第三方清单齐全（open-xiaoai / EchoMuse / sherpa-onnx / SenseVoice / silero-vad / speexdsp / FFmpeg） |
- `docs/ROADMAP.md:79` | P2.1 ✅ | **`docs/socket-plug.md` 标准 v0**：插头 manifest（机型/A/B 等级/能力/参数 schema/协议/兼容版本/交付物+可选 adapter）、打包与校验、A/B 最小清单、信任模型、运行形态（in-process vs sidecar） | 标准文档可让第三方不看源码写出插头；LX06 与 dot2 都能套进去 |
- `docs/ROADMAP.md:80` | P2.2 ✅ | **安装向导设计稿 + 后端切片**：`docs/install-wizard-design.md`（`5c7a0c4`）；`GET /plugs`、`/setup/step`（证据驱动 + setup.json + allowlist）与内置 LX06 manifest（`cfea073`）；UI 进行中 | 设计稿评审通过；后端 110 测试通过 |
- `docs/ROADMAP.md:91` | P3.1 ✅ | 契约核心 + 测试工具 + LX06 参考插头：`DeviceWake/drain_wakes`、manifest v0/v1、音频策略/metrics/trace、`rui plug-test`、`plugins/rui-plugin-xiaoai` | 948→962 tests；LX06 v0/KWS 不回归；参考插头 kit 16 PASS |
- `docs/ROADMAP.md:109` | P5.1 ✅ | Phase 1：config v3（`devices[].audio` 覆盖、`multi_device` 块、`effective_audio()` 继承、引用/房间校验、v2 文件保持 v2） | `tests/test_config_v3.py` 21 用例；全量 1173/18；LX06 行为零差异 |
- `docs/ROADMAP.md:122` | 真机 | LX06 + 2×Dot2：共存 `devices=2` 且双 `speaker_connected`；各自房间唤醒；同词双命中只产生 1 个 turn；播放中 barge 只归播放设备；杀一个平面其余可用；`multi_device.enabled=false` 回滚后 = 0.4.x 语义；记录 2/4 平面 RSS/CPU 与唤醒→首字延迟 |
- `docs/ROADMAP.md:133` **生产已升级（2026-10-25 18:02，9哥 点头）**：`RUI_IMAGE=ghcr.io/yes999zc/rui:0.5.0`，healthy、uid 1000；`/health` version 0.5.0、config_version 仍 2、`audio_devices` living/客厅 running+connected、healthcheck ok、真实回合 `audio_bytes=120,912`；`multi_device.enabled=false`。备份 `.env.bak.20261025-180259`、`config/config.yaml.bak.20261025-180259`、`/vol1/rui-backups/rui-backup-20261025-180734.tar.gz`（sha256 `17711958…`）。真机 LX06+2×Dot2 验收与回滚演练待 9哥 安排。
- `docs/ROADMAP.md:137` 默认建议：内置 LX06 in-process；第三方插头默认 sidecar，除非签名并声明可信。
- `docs/acceptance.md:7` > `docs/open-xiaoai-client.md`（音箱接线）、`docs/channel-gateway.md`（Telegram/微信）。
- `docs/acceptance.md:42` | 音箱 client | 按 `docs/open-xiaoai-client.md` 填 `ws://<NAS-IP>:4412/?token=<device-token>` | 音箱日志 connected；`/health` `speaker_connected=true` | ✅ connected=true（期间被另一台调试设备抢占同一入口，已停） |
- `docs/ack.md:8` On an LX06 the microphone is muted for about 1.2 s while the speaker plays. A spoken
- `docs/ack.md:105` of it, all of it: 0 utterances in 20 rounds. The LX06 mutes its microphone for ~1.2 s
- `docs/adapter-contract.md:23` | `inbound` | the device dials us | LX06: `ws://<server>:<listen_port>/?token=...` |
- `docs/adapter-contract.md:45` `play()` takes the layout because playback is not always mono 16 kHz: the LX06
- `docs/adapter-contract.md:61` features, including barge-in. Where the LX06 adapter sits.
- `docs/adr/0005-audio-layout-and-provider-chains.md:8` Then the hardware was measured: an LX06 captures `S32_LE`, 8 channels at 48 kHz with
- `docs/architecture.md:33` │ L7 users + hardware          LX06 · the next speaker · any box that can SSH  │
- `docs/architecture.md:101` `reference_channels`, so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32
- `docs/architecture.md:158` | ~~`AdapterName = Literal["xiaoai","mock"]`~~ (fixed in 0.3: adapter is a free string) | `config/schema.py` |—|
- `docs/archive/HANDOFF-history-to-2026-10-24.md:5` > 1. **老项目 `xiaoai-voice-entry` 的 `docs/design-and-measurements.md`**
- `docs/archive/HANDOFF-history-to-2026-10-24.md:42` **任何能 SSH 的音箱都能接的个人贾维斯，NAS 一键部署。** v1 只支持小米小爱音箱 Pro（LX06）。
- `docs/archive/HANDOFF-history-to-2026-10-24.md:46` 前端 Vue 3 + TS + Vite。旧项目 `xiaoai-voice-entry` **只读参考**：不 import、不改配置、
- `docs/archive/HANDOFF-history-to-2026-10-24.md:52` 设备(LX06) --ws 拨入--> device adapter --> 音频面进程 --> IPC(unix socket) --> 控制面(FastAPI)
- `docs/archive/HANDOFF-history-to-2026-10-24.md:69` | S2 | LX06 入站 adapter、线协议、假音箱 e2e | ✅ 真机采集 246 帧 |
- `docs/archive/HANDOFF-history-to-2026-10-24.md:111` 8. **厂家唤醒 = 让位**（`session.vendor_quiet_s` 12 s）：说「小爱同学」时不开我们的会话、
- `docs/archive/HANDOFF-history-to-2026-10-24.md:216` - 厂家让位（§4.8）：说「小爱同学」时我们不开会话、不播提示音。
- `docs/archive/HANDOFF-history-to-2026-10-24.md:252` **真机切换**（LX06，`root@$SPEAKER_IP`，密码从 `$SPEAKER_PASSWORD` 读，必须
- `docs/archive/HANDOFF-history-to-2026-10-24.md:262` **注意**：SSH 命令里不要出现 `pkill -f "/data/open-xiaoai/client"` 这种字面量——它会匹配到
- `docs/archive/HANDOFF-history-to-2026-10-24.md:263` 自己的 ssh 命令行，把会话打断。用 `pkill -f 'open-xiaoai/clien[t]'`。
- `docs/archive/HANDOFF-history-to-2026-10-24.md:310` 已对）/ CREDIT 拉模式（已抄）/ 「全音量下只有厂家 AEC 听得清人声，小爱同学是打断音乐的方式」——
- `docs/archive/HANDOFF-history-to-2026-10-24.md:351` 句式）；V4 让位（小爱唤醒→歌停→还回，ubus 注入法见 vendor.md）；远场 20 唤醒计数；灯环
- `docs/archive/HANDOFF-history-to-2026-10-24.md:365` 4. **V4 反向让位**：小爱答天气 15 s，我方音乐压低 16 s，`playing Idle` 后 0.6 s 还歌
- `docs/archive/HANDOFF-history-to-2026-10-24.md:377` **2026-10-23 上午补充（音量与小爱「哎」）**：
- `docs/archive/HANDOFF-history-to-2026-10-24.md:384` - **小爱「哎」时刻让位**：厂家最终转写要 3 s 后才到，但中间识别结果（`vendor_listening`）在唤醒
- `docs/archive/HANDOFF-history-to-2026-10-24.md:520` - **playback 默认 48000/2ch 是错的**：LX06 客户端只收 mono，adapter 会拒绝 → 默认改成
- `docs/archive/HANDOFF-history-to-2026-10-24.md:1162` `docs/open-xiaoai-client.md`、安全响应头/鉴权失败日志、用户数据 0600、backup/restore、
- `docs/archive/HANDOFF-history-to-2026-10-25.md:11` | 项目 | 自托管语音终端：LX06 音箱 + FastAPI 控制面 + 独立音频面 + Vue 控制台；MIT；公开仓 `yes999zc/rui` |
- `docs/archive/HANDOFF-history-to-2026-10-25.md:29` - `rui_compat` 上界抬到 `<0.7.0`（内置 LX06 / example / v1 文档 / 测试；`a4740b1`）——不改会让 0.5.0 拒绝加载内置插头；
- `docs/archive/HANDOFF-history-to-2026-10-25.md:36` - **下一步**：**生产部署 v0.5.0 需 9哥 点头**（备份 `.env`/config → 改 `RUI_IMAGE` → `up -d` → `/health` devices=2 验收）；真机 LX06+2×Dot2 验收与回滚演练等 9哥 安排；dot2 会在发布后做干净安装验收（卡点单独开 issue）。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:48` 4. **v0.5.1 发布 ✅**（2026-10-25 18:5x，dot2 #13/#14）：tag `v0.5.1`（commit `c0d7048`）、digest `sha256:25d67cf88c62…`（0.5.1=`0.5`=`latest`=`sha-c0d7048` 同一 manifest，amd64+arm64）；CI 全绿；Release 已发；#7/#13/#14 已通知复测。**生产保持 0.5.0**（0.5.1 只改安装器/doctor/文档，无运行时变化）。**剩真机 LX06+2×Dot2 验收与回滚演练（9哥 安排）**。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:50` 6. **真机验收**（9哥 保留）：LX06 + 2×Dot2 的共存/各自唤醒/同词单 turn/barge 归属/故障隔离/回滚；记录 2/4 平面 RSS/CPU 与唤醒→首字延迟。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:65` 设备(LX06) --ws 拨入 :4412--> device adapter --> 音频面进程 --IPC(unix socket)--> 控制面(FastAPI :8080)
- `docs/archive/HANDOFF-history-to-2026-10-25.md:104` 8. **厂家让位**：说「小爱同学」时不开我们的会话、不播提示音；我们的唤醒词立即接管。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:113` 内置 LX06 作为参考插头；第三方插头默认 sidecar 容器隔离，签名且声明可信的才 in-process。标准文件 `docs/socket-plug.md`（P2.1 起草）。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:114` 15. **音箱端依赖上游**：open-xiaoai（[coderzc/open-xiaoai](https://github.com/coderzc/open-xiaoai)）；我们验证用的 fork 分支在文档里写清最低 client 版本。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:115` 16. **ffmpeg 换 LGPL-only 构建**；MIT 只覆盖本项目代码，第三方组件进 `THIRD_PARTY.md` 并在 README 致谢（open-xiaoai / EchoMuse / sherpa-onnx / SenseVoice / silero-vad / speexdsp / FFmpeg）。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:116` 17. **0.1.x 不拆 LX06**：核心保留 `device/xiaoai` 作为参考插头；0.2 再抽成独立包，避免契约未定稿就拆。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:165` - LX06 抽成 `rui-plugin-xiaoai`；`rui-dot2-bridge` 按标准改造成第一个外部 B 类插头
- `docs/archive/HANDOFF-history-to-2026-10-25.md:198` - 10-25 凌晨：P3.0–P3.4 完成并发布 0.3.0（生产暂留 0.2.2：NAS 拉 GHCR TLS 超时，网络恢复后升级）：Socket/Plug v1（device_wake 契约、契约测试工具、LX06 参考插头）、
- `docs/archive/HANDOFF-history-to-2026-10-25.md:215` - 10-24 下午：确定 Socket & Plug 为长期架构（声明必需 + 可选代码包；内置 LX06 参考插头；第三方默认 sidecar）；
- `docs/archive/HANDOFF-history-to-2026-10-25.md:216` 定 ffmpeg 走 LGPL-only；定上游 open-xiaoai；派智能体调研 EchoMuse Device Management；路线图按“基础设施优先”重排。
- `docs/archive/HANDOFF-history-to-2026-10-25.md:230` - 音箱接入：`docs/open-xiaoai-client.md`；剩余工作：`docs/remaining-work.md`；路线图：`docs/ROADMAP.md`
- `docs/audio-latency.md:3` 真机（LX06）+ 假音箱两条链路都测过。每个数字都来自当天日志，复现命令在文末。
- `docs/audio-pipeline.md:9` | capture | 48000 | 8 ch, 32 bit | what the LX06 actually sends |
- `docs/audio-pipeline.md:11` | playback | 48000 | 2 ch, 16 bit | what the LX06 amplifies |
- `docs/audio-pipeline.md:68` ### On the speaker (LX06, 2026-10-21, `scripts/measure_erle.py`)
- `docs/audio-pipeline.md:110` Measured on the LX06 with music playing (`scripts/measure_wake.py`, 75 s, wake word
- `docs/console-audit.md:48` | `/panel/devices` | 286 字 | 0 | 0 | `客厅 · living` / `<音箱内网 IP> · xiaoai · LX06 · ready` + 音量 178/255 |
- `docs/console.md:4` 控制台全文报告（本文件为蒸馏，老项目引用均指 xiaoai-voice-entry，只读）。
- `docs/d1c-notes.md:24` - `xiaoai-voice-entry/server/library_index.py` + `docs/library-index.md`：每首歌语言索引的完整规则、
- `docs/d1c-notes.md:164` 老项目行号（都指 `xiaoai-voice-entry/server/`）：
- `docs/deploy.md:12` RUI 的安装起点是「音箱已经能运行 open-xiaoai client，并能回连 NAS」。**刷机、拿 root/SSH、安装 client 都不属于 RUI**：按 [open-xiaoai](https://github.com/coderzc/open-xiaoai) 项目完成；RUI 不刷机、不托管固件，也不会在音箱上执行命令。v1 只支持 **Xiaomi 小爱音箱 Pro（LX06）**，音箱与 NAS 在同一 LAN/L2，client 回连 NAS 的 `4412`。
- `docs/deploy.md:20` | 1 | 音箱型号/固件版本，open-xiaoai client 的版本或 commit | `prepare` 兼容性确认、排障、回滚 |
- `docs/deploy.md:23` | 4 | client 安装路径（常见 `/data/open-xiaoai/`） | 重启 client、找日志、卸载/回滚 |
- `docs/deploy.md:273` **这一步做什么**：显示 open-xiaoai 的链接、最低版本、安装步骤和回滚说明；只让你勾选确认，
- `docs/deploy.md:290` | 灯效 | 新 LX06 默认开；关掉则灯环不亮 | 开 |
- `docs/deploy.md:365` 正常不用手写：向导 `params` 步会 merge 写入第一台设备。LX06 新设备**默认开灯环**
- `docs/deploy.md:372` adapter: lx06            # 0.6 起用 manifest 的机型 id；旧配置的 adapter: xiaoai
- `docs/deploy.md:381` enabled: true           # 向导创建的新 LX06 默认开灯环
- `docs/deploy.md:558` NAS 日志是 `xiaoai: rejected ... (bad or missing token)`。
- `docs/deploy.md:586` playback: {rate: 16000, channels: 1}    # LX06 的 wire
- `docs/deploy.md:587` aec: {enabled: true, who: controller}   # LX06 有参考通道，RUI 做 AEC
- `docs/deploy.md:590` - id: living            # 客厅 LX06：全部继承全局
- `docs/deploy.md:591` adapter: lx06          # 旧配置写 xiaoai 也可（单 minor 别名）
- `docs/deploy.md:618` | `audio.capture.rate` / `channels` / `ref_channels` | 采集布局；LX06 是 48k/8ch/6mic+2ref，Dot 2 是 16k/1ch/0ref | 见插件 manifest |
- `docs/deploy.md:758` 0.5.0 会让 `>=0.3.0 <0.5.0` 直接失效；内置 LX06 / `examples/plugin-example` 已抬到 `<0.7.0`，
- `docs/deploy.md:803` ### 7.4 混合房子的每设备覆盖（LX06 + Dot 2）
- `docs/device-management-research.md:111` **为什么 RUI 不能照搬**：LX06（A 类）没有 root、不能刷机；Dot2（B 类）的刷机在
- `docs/device-management-research.md:340` | 设备 | 只有 Echo Dot 2（B 类，root，EchoMuse 固件） | LX06（A 类，无 root，open-xiaoai client）+ Dot2（B 类，外部插头） |
- `docs/device-management-research.md:341` | 设备连接 | 设备主动拨 controller，三条 ws 平面 | 设备主动拨 4412；端口内协议由 adapter 决定（LX06 一条 ws；dot2 是 `/control` + `/data`） |
- `docs/device-management-research.md:413` 3. **设备侧动作不可执行就降级为「说明 + 链接 + 证据」**：A 类（LX06）给 open-xiaoai client
- `docs/device-management-research.md:520` - `prepare`：A 类 = open-xiaoai client 安装说明（[RUI-OX L19-31]）；B 类 = 设备侧仓库链接。
- `docs/device-management-research.md:579` | `GET /api/v1/setup?plug={id}` | query | `SetupState`（扩展 `plug` + 8 步） | 不传 `plug` = 现有默认（xiaoai） |
- `docs/device-management-research.md:669` 2. `src/rui/device/plugs/xiaoai.plug.json`、`dot2.plug.json`：两个最小 manifest（~80 行/个）。
- `docs/device-management-research.md:824` | 适合 | 内置 LX06、审过的 dot2 | 第三方/未签名插头 |
- `docs/device-management-research.md:843` - `/setup`（现有）：顶部插头选择（`LX06（A 类，内置）` / `Dot2（B 类，外部插头）`）→ 8 步骨架；
- `docs/device-management-research.md:857` - 老 `config.yaml`：`params` 缺省 `{}`；`adapter: xiaoai` 自动匹配内置 manifest；无需迁移。
- `docs/device-management-research.md:876` 5. **向导先服务 LX06 + Dot2 两类**：A 类给安装说明 + ws URL；B 类给外部链接 + 参数 + 连接测试；
- `docs/device-management-research.md:950` | RUI-OX | `docs/open-xiaoai-client.md` | LX06 client 安装、ws URL/token、常见故障（L7-49） |
- `docs/first-run.md:15` Rui 的起点是「音箱已经运行 open-xiaoai client，并能回连 NAS」。刷机、root/SSH、装 client
- `docs/first-run.md:16` 不属于 Rui：按 [open-xiaoai](https://github.com/coderzc/open-xiaoai) 完成。v1 只支持
- `docs/first-run.md:17` **Xiaomi 小爱音箱 Pro（LX06）**，音箱与 NAS 在同一 LAN/L2，client 回连 NAS 的 `4412`。
- `docs/first-run.md:21` | 1 | 音箱型号/固件版本，open-xiaoai client 的版本或 commit | `prepare` 兼容性确认、排障、回滚 |
- `docs/first-run.md:24` | 4 | client 安装路径（常见 `/data/open-xiaoai/`） | 重启 client、找日志、卸载/回滚 |
- `docs/first-run.md:111` | 3 | `prepare` | 拿出上面的清单：client 已装、IP 已固定、SSH/重启/日志/回滚都记下 | 展示 open-xiaoai 链接和机型检查清单；不执行任何设备命令 | 设备侧项目勾完并确认；token 留到 `connect` |
- `docs/first-run.md:147` | 灯效 | 0.4.0 新建 LX06 默认打开；不想要就关掉 |
- `docs/first-run.md:166` | client 日志 401 / close 1008，NAS 日志 `xiaoai: rejected ... (bad or missing token)` | 两边 token 是否同一个 | 在 `connect` 重新复制当前 token，更新 client，重启 client；不是控制台 token |
- `docs/first-run.md:223` | 灯环不亮 | 0.4.0 新建 LX06 默认 `led.enabled: true`；到 `params` 的“灯效”或设备配置确认。老配置升级上来可能仍是关闭状态 |
- `docs/first-run.md:234` Rui starts from "the speaker already runs the open-xiaoai client and can dial back to the
- `docs/first-run.md:236` the [open-xiaoai](https://github.com/coderzc/open-xiaoai) project for that. v1 supports
- `docs/first-run.md:237` only the **Xiaomi AI Speaker Pro (LX06)**, on the same LAN/L2 as the NAS, dialing back on
- `docs/first-run.md:242` | 1 | Speaker model/firmware, plus the open-xiaoai client version or commit | `prepare` compatibility check, troubleshooting, rollback |
- `docs/first-run.md:245` | 4 | Client install path (usually `/data/open-xiaoai/`) | Restarting the client, finding logs, uninstall/rollback |
- `docs/first-run.md:337` | 3 | `prepare` | Client installed, IP reserved, SSH/restart/logs/rollback recorded | Shows the open-xiaoai link and device checklist; runs nothing on the speaker | Device-side items confirmed |
- `docs/first-run.md:373` | LED | New LX06 devices default to on in 0.4.0; switch it off if you do not want the ring |
- `docs/first-run.md:397` | Client logs 401 / close 1008; NAS logs `xiaoai: rejected ... (bad or missing token)` | Whether both sides use the same token | Copy the current token from `connect`, update the client, restart it; this is not the console token |
- `docs/first-run.md:464` | LED ring stays off | New LX06 devices default to `led.enabled: true` in 0.4.0; check the `params` LED toggle or the device config. An upgraded old config may still have it off |
- `docs/install-flow-audit.md:3` > 只读审计，未改代码、未提交。视角是“第一次装、没有 NAS 经验、只有 LX06 和必要密钥”的用户。
- `docs/install-flow-audit.md:5` > `docs/open-xiaoai-client.md`、`docs/volc-credentials.md`、`setup_api.py`、`SetupView.vue`、`i18n.ts`、`builtin.py`。
- `docs/install-flow-audit.md:17` | 6 | connect | 复制 ws 地址/token 到 client，等回连 | 只读展示；`connect.inspect` 不写配置、不做探测 | URL host 取自浏览器 Host：用 localhost/SSH 隧道打开时给出 `ws://127.0.0.1:4412`，永远连不上；TLS 反代下给 `wss://`，LX06 client 未必支持 | ❌ “测试连接”是空操作（`setup_api.py:923-924`） |
- `docs/install-flow-audit.md:32` 5. **params 文案承诺了不存在的控件。** 用户读到的“音量、灯效、监听端口、麦克风增益”（`i18n.ts:522,524`）在 LX06 manifest 里根本没有；同一张卡片又写“没展示的字段会保留”（`i18n.ts:550`）。隐藏默认值与文档相反：新设备 LED 被强制打开（`setup_api.py:869-874`），文档仍写 `enabled: false`（`docs/deploy.md:118`）；schema 音量默认 60（`schema.py:130`），docs 写 0，设备页文案又写 0–255（`i18n.ts:676`）。
- `docs/install-flow-audit.md:48` 18. **文档/版本小漂移。** README badge 与固定版本示例还是 0.2.0（`README.md:11,105`），HEAD 是 0.2.1；`docs/deploy.md:251` 仍把“没设 `web.auth_token`”列为可接受的 degraded，而 `rui setup` 会生成 token、非 loopback 空 token 直接拒绝启动（`__main__.py:105-108,177-187`）；`docs/open-xiaoai-client.md:22` 的 “Setup → Speaker” 已不存在（步骤叫 connect）。
- `docs/install-wizard-design.md:10` > 新建设备写插头 manifest 的 device id 作为 `adapter`（当前 LX06 为 `lx06`）；旧配置的
- `docs/install-wizard-design.md:11` > `adapter: xiaoai` 通过 `src/rui/adapter_aliases.py` 一个 minor 的兼容别名继续解析。
- `docs/install-wizard-design.md:158` {"plug":{"id":"xiaoai","version":"0.2.0","class":"A","adapter":"xiaoai","transport":"inbound","source":"builtin","available":true,"verified":true},
- `docs/install-wizard-design.md:161` "device":{"id":"living","adapter":"xiaoai","host":"<speaker-ip>","room":"客厅","wake_words":["你好小瑞"],"enabled":true,"params":{}},
- `docs/install-wizard-design.md:171` {"plug":"xiaoai","step":"params","values":{"host":"<speaker-ip>","room":"客厅",
- `docs/install-wizard-design.md:284` | | A 类（LX06，原厂共存） | B 类（Echo Dot 2，root/刷机） |
- `docs/install-wizard-design.md:287` | 向导动作 | 给 open-xiaoai 链接、最低版本、SSH 前提、卸载/恢复说明 | 给解锁/刷机/回滚链接；**不代刷、不托管固件** |
- `docs/install-wizard-design.md:300` adapter: xiaoai
- `docs/install-wizard-design.md:334` | 1 | `test_plugs_list_builtin_and_broken_manifest_is_not_fatal` / `test_plug_detail_and_unknown_plug` | `xiaoai` 可用；坏 YAML 只进 `error`；详情含 `device_side`/`params`/`class`；未知 id 404 |
- `docs/install-wizard-design.md:356` ### 8.3 手工验收：LX06（A 类）
- `docs/install-wizard-design.md:358` 前置：LX06 已开 SSH，NAS 与音箱同 LAN；`install.sh` 已生成 `.env`；用 `<speaker-ip>` 代指真实地址。
- `docs/install-wizard-design.md:360` 1. 打开 `/setup`，页头显示 LX06 / A 类 / 已验证或未验证徽标。
- `docs/install-wizard-design.md:363` 4. `prepare`：按 open-xiaoai 链接装 client，勾选完成；LX06 不出现 B 类确认。
- `docs/install-wizard-design.md:385` 4. **P2.1 loader 尚未落地**：本设计假设至少有 `xiaoai.yaml`、`mock.yaml`、`dot2.yaml`；若延期，先用 `tests/fixtures` manifest 跑 API，UI 只显示 xiaoai。
- `docs/install-wizard-design.md:410` | 3 | adapter→manifest 命名冲突 | P2 先加 `DeviceConfig.params: dict`（保留键 `plug_id`）并保留 `devices[].adapter` 给内置 LX06；P3 抽包时再加 `DeviceConfig.plug` 并兼容旧字段。 |
- `docs/latency.md:134` `xiaoai-voice-entry` runs in production instead of guesses:
- `docs/multi-device-design.md:87` | LX06 | inbound ws，4412，query token | rui KWS（持续上行 8ch/48k） | 6 mic + 2 ref；全局 audio.capture 按它量；AEC 在 rui |
- `docs/multi-device-design.md:102` 7. 新：**隐私显式**。LX06 这类 KWS 在 rui 的音箱会持续上行；device_wake 的开放设备只在唤醒后上行。多房间部署时，文档必须把这个差异说清楚，控制台也要能看出每台设备的唤醒来源。
- `docs/multi-device-design.md:116` │ 音频面 lx06  │ │ 音频面 dot2  │ │ 音频面 ...   │
- `docs/multi-device-design.md:121` 客厅 LX06       书房 Dot2
- `docs/multi-device-design.md:127` - 优点：故障隔离（一台崩不影响其他）、每设备 AEC/采集布局独立（LX06 有 ref、Dot2 无 ref）、每设备独立端口/证书/token、符合现有 supervisor 进程模型、可逐台启用/回滚。
- `docs/multi-device-design.md:150` **方案 A 第一版**：中枢仲裁 + 每设备一个音频面；房间平权；可选 fallback output；全局 one-turn/one-output。先在 LX06 + Dot2 两台上验证，再推广到 3–4 台。
- `docs/multi-device-design.md:189` - 设备侧 AEC（Dot2）与 rui AEC（LX06）必须能按设备选择：Dot2 用 devices[].aec.who=device + followup_delay_ms=2500/followup_replay=false（0.4.1 已有全局版本，v3 下沉到每设备）；LX06 保留 controller AEC，不能因为引入多设备就把现网 25.2 dB 的 AEC 路径切走（dot2 aec-parameter-model.md §8.3 的警告）。
- `docs/multi-device-design.md:233` - id: lx06
- `docs/multi-device-design.md:234` adapter: lx06            # 旧配置的 adapter: xiaoai 仍是单 minor 别名
- `docs/multi-device-design.md:236` host: lx06.local
- `docs/multi-device-design.md:257` default_output: lx06          # 无来源输出（提醒/通知）的 fallback；可选
- `docs/multi-device-design.md:263` room_priority: [lx06, dot2] # 显式优先级；缺省按 devices 顺序
- `docs/multi-device-design.md:278` LX06 的 8 ch / 6 mic / 2 ref / 48 kHz 由它自己的 plug manifest
- `docs/multi-device-design.md:279` （`plugs/xiaoai/devices/lx06.yaml`，镜像内 `/opt/rui-plugins`）声明，不再硬编码在
- `docs/multi-device-design.md:299` | audio.aec.who | device｜controller｜both｜none | controller（LX06 现状） | v3 新；device 时 rui 不跑 AEC，依赖设备；none 只允许 ASR 无 barge |
- `docs/multi-device-design.md:300` | devices[].audio.voice.followup_* | 同全局 | 继承 | Dot2 用 2500/false；LX06 保持 1000/true |
- `docs/multi-device-design.md:329` - **端口**：每台设备的平面绑定自己的 effective_listen_port（LX06 4412 / Dot2 8870），互不冲突；不再需要协议 demux，因为 adapter/port 已一一对应。
- `docs/multi-device-design.md:347` - 每平面重复加载 KWS/VAD/AEC 模型（LX06 侧数 MB 级 ONNX；Dot2 侧 KWS 若也启用）；4 台以内预计可控，但需要在 NAS 上实测 RSS/CPU（§7 验收）。
- `docs/multi-device-design.md:350` - 隐私：LX06 持续上行（KWS 在 rui）；Dot2 的 device_wake 就位后只在唤醒后上行。控制台应显示每台设备的 wake.source 与“是否持续上行”，让用户知道自己开了什么。
- `docs/multi-device-design.md:368` - **契约/插头**：既有 rui plug-test 不回归；fake adapter 注入两个房间的 DeviceWake；LX06（KWS）与 Dot2（device_wake）混合候选。
- `docs/multi-device-design.md:370` ### 7.2 真机验收（LX06 + Dot2）
- `docs/multi-device-design.md:379` | 故障隔离 | 杀掉 Dot2 平面 → 只有书房掉线；LX06 仍可唤醒回答；supervisor 自动重启并恢复 |
- `docs/multi-device-design.md:393` - 保持 0.4.1 的修复稳定：effective listen_port/token_env、followup 2500/false（需要时）、wake re-probe；LX06 默认 1000/true 不动。
- `docs/multi-device-design.md:405` per-device AEC 解析有测试；LX06 不回归。
- `docs/multi-device-design.md:407` ### Phase 2（随 v0.5.0 发布）：多平面 + 中央仲裁（目标：LX06 + Dot2）
- `docs/multi-device-design.md:458` | D9 | 回滚 | Phase 2 起生产默认 `multi_device.enabled=false`；LX06+Dot2 验收通过后才开 |
- `docs/multi-device-design.md:493` - **4412 不再有“默认设备口”语义**：端口一律来自 `devices[].listen_port` / manifest `runtime.listen_port`（0.4.1 的 `effective_listen_port` 已修诊断侧）；LX06 4412、Dot2 8870 并存。
- `docs/multi-device-design.md:603` - `rui_compat` 的**匹配语义不变**（range 匹配包版本），但 0.5.0 会让 `<0.5.0` 失效：下游插头需在 0.5.0 发布前把上界抬到 `<0.7.0`（内置 LX06 与 example 同步抬）。
- `docs/multi-device-design.md:612` - 真机：LX06 + 2 台 Dot2 同在线、各自唤醒、同时听到只一个 turn、播放中打断只归播放设备、
- `docs/music.md:166` 本文档引用的所有 文件:行号 均指 `老项目 xiaoai-voice-entry/server/`（只读参考）。
- `docs/open-xiaoai-client.md:1` # 音箱客户端接入（open-xiaoai）
- `docs/open-xiaoai-client.md:3` 小瑞不修改音箱固件；音箱上运行 **open-xiaoai client**，主动拨回小瑞的音频面。服务端部署完成后，按下面顺序接线。
- `docs/open-xiaoai-client.md:5` > 不同 open-xiaoai 版本的安装脚本/路径略有差异；以你手上版本的 README 为准。本文只约定小瑞需要的连接参数。
- `docs/open-xiaoai-client.md:13` - [ ] **client 安装路径**：常见 `/data/open-xiaoai/`；保留原始安装包/commit，方便回滚。
- `docs/open-xiaoai-client.md:17` - [ ] **重启 client 的命令**和**日志位置/查看命令**（例如重跑 `/data/open-xiaoai/` 下的启动脚本，`tail -f` 对应日志）。
- `docs/open-xiaoai-client.md:34` 1. 按 open-xiaoai 项目说明把 client 安装到音箱（通常放在 `/data/open-xiaoai/`）。
- `docs/phrasebook-editor.md:5` > 老项目引用均指 `xiaoai-voice-entry/server/`，只读。
- `docs/plan-to-0.6.md:8` - 生产：`/vol1/rui`，`rui-dot2:0.5.2-hot4`，devices=2（客厅 LX06 + 卧室 Dot2），`multi_device.enabled=true`，`config_version=3`。
- `docs/plan-to-0.6.md:41` - `.34` 金丝雀 24 h（LX06 + Dot2 两台），8 项真机验收：唤醒→应答、告别只在发起房间、连喊三次都进、ack 不自听、打断、重启重连、LX06 无回归、回滚 0.5.0；
- `docs/plan-to-0.6.md:48` - 核心默认改中性（16000 / 1ch / 0ref / earcon），LX06 的 8ch/6mic/2ref 由它自己的 manifest 声明；
- `docs/plan-to-0.6.md:52` - `src/rui/device/xiaoai/` → `plugs/xiaoai/`，走与 dot2 相同的 manifest 加载；`adapter: xiaoai` 保持不变，镜像自带，**现有安装零迁移**；
- `docs/plan-to-0.6.md:53` - 去掉 core 对 xiaoai 的硬引用（`plugins/builtin.py`、`registry.py`、catalog 文案、web 默认值）；
- `docs/plan-to-0.6.md:58` - 现有 config v3 原样启动、LX06 行为零差异；
- `docs/plan-to-0.6.md:79` - **LX06 回归**：plug 化保留一个 minor 的内置兼容；金丝雀不过不上生产。
- `docs/playback-path.md:17` 设备上跑的是 `yes999zc/open-xiaoai`（fork 分支 `bakey/device-events`），源码在
- `docs/playback-path.md:18` `老项目 xiaoai-client/packages/client-rust`。关键文件
- `docs/playback-path.md:39` ## 2. 老项目的做法（`xiaoai-voice-entry`，跑过两天真机）
- `docs/playback-path.md:170` - **范围 0–1000 ms，默认 0。** 默认 0 不是"没实现"，而是保留现有 LX06 行为：
- `docs/playback-path.md:171` LX06 的控制面/TTS 把一句一次性推进来，Pacer 250/320 ms 的提前量已经够用，
- `docs/playback-path.md:208` `stop_playback()`；否则照旧发送。LX06 上 `play()` 自己会发 `start_play`，行为不变。
- `docs/playback-path.md:215` 适用对象：控制面与数据面是两条 socket 的适配器（Echo Dot 2 等）。LX06 的同一条
- `docs/playback-path.md:222` 沿用现有 stop/start 路径；为 True 时走下面的契约。这样不会破坏 LX06。
- `docs/playback-path.md:249` - LX06（`ordered_start: false`）行为不变，既有 `test_turn_audio` / `test_playback`
- `docs/plug-test-kit.md:64` `plugs/xiaoai` 的，adapter 由 manifest 的 `runtime.entrypoint` 注册，不需要
- `docs/plug-test-kit.md:105` 真实设备的参考插头（LX06，v1/RUI KWS）就是镜像自带的 `plugs/xiaoai/`
- `docs/plug-test-kit.md:106` （0.6 M2：核心不再有 `src/rui/device/xiaoai/`）：
- `docs/plug-test-kit.md:109` python -m rui plug-test plugs/xiaoai
- `docs/plug-test-kit.md:110` python -m rui plug-test plugs/xiaoai \
- `docs/plug-test-kit.md:111` --adapter xiaoai_plug.adapter:build --json
- `docs/plug-test-kit.md:214` inbound 插头（如 LX06）的 `probe()` 和 adapter 表面可以在没有设备时验收，但
- `docs/plug-test-kit.md:219` `plugs/xiaoai/xiaoai_plug/adapter.py`。
- `docs/recon-old-build.md:3` > 两个只读侦察 agent 去 `xiaoai-voice-entry` / `xiaoai-client` 抄回来的参数与实现。
- `docs/recon-old-build.md:52` - 缓存：`/tmp/xiaoai_online/<id>.mp3|flac`（<200 KB 判失败）→ `ffmpeg -f s16le -ac 1 -ar 16000` 成 `.pcm`；45 s 下载超时。
- `docs/recon-old-build.md:71` - 我们的形状：新增 `device/xiaoai/led.py`（脚本构建器 + LedRing）、`Capability.LED`、`DeviceServices.set_led`；执行放音频面（fire-and-forget），控制面只发状态。配置放 **per-device**（`devices[].led`），不学旧项目的全局 `led:`。
- `docs/releases/v0.4.0.md:5` rui turns a Xiaomi LX06 speaker into a self-hosted voice terminal: a FastAPI control
- `docs/releases/v0.4.1.md:10` defaults to the measured LX06 behaviour, and the API surface is unchanged.
- `docs/releases/v0.4.1.md:51` Device management used to assume the LX06 defaults (`4412`, `RUI_DEVICE_TOKEN`) when a
- `docs/releases/v0.4.1.md:61` - Port-conflict detection uses the manifest port, so dot2 on 8870 and an LX06 on 4412 no
- `docs/releases/v0.4.1.md:65` `runtime` → loader-cached default → LX06 literals.
- `docs/releases/v0.4.1.md:70` what it heard in between. That one second is the LX06's measured echo tail; a plug that
- `docs/releases/v0.4.1.md:81` The defaults preserve the LX06 path exactly, and the follow-up capture buffer is now
- `docs/releases/v0.4.1.md:109` config keeps the LX06 defaults (1000 ms, replay on).
- `docs/releases/v0.5.0.md:156` | Real devices | LX06 + 2 x Dot2 | reserved for 9哥; recorded separately from this table |
- `docs/releases/v0.5.3.md:5` 0.5.3 is the stabilization release for the first multi-device production install: one LX06
- `docs/releases/v0.5.3.md:15` override (the Dot 2 wire is 48 kHz mono, the LX06 is 16 kHz) was raised at speak time: the
- `docs/releases/v0.5.3.md:23` the LED used the *primary* client, so the LX06 said farewell for a Dot 2 turn in the
- `docs/releases/v0.5.3.md:93` | Real devices | `.34` canary (synthetic Dot 2 client + real LX06 absent) | PENDING |
- `docs/releases/v0.6.0.md:5` The device-agnostic core. rui no longer names a speaker: the LX06 adapter is a plug loaded
- `docs/releases/v0.6.0.md:12` ### 1. The LX06 is a plug, and the core names no device
- `docs/releases/v0.6.0.md:14` plugs/xiaoai ships a plugin manifest and a rui-device/v1 manifest; the image copies it to
- `docs/releases/v0.6.0.md:16` `adapter: xiaoai` keeps working through a single alias (src/rui/adapter_aliases.py, gone no
- `docs/releases/v0.6.0.md:18` mention xiaoai outside that alias.
- `docs/releases/v0.6.0.md:24` (16000/1ch/0ref, earcon ack) and the LX06 manifest states the 48000/8ch/6mic/2ref the core
- `docs/releases/v0.6.0.md:48` - Mixed houses (LX06 + Dot 2): the global `audio.capture` still wins over a plug's manifest
- `docs/releases/v0.6.0.md:71` | Production | LX06 + Dot 2, `/health` | version 0.6.0, devices=2, device_errors=0 |
- `docs/remaining-work.md:34` （ws URL/token/复制/未配置提示）+ `docs/open-xiaoai-client.md`；**冷装已真机演练通过**（423 s 到第一句）。
- `docs/remaining-work.md:56` 0.2.1 决定：LX06（有 led 能力）模板默认 `enabled: true`，并在向导「音箱参数」里显式提供灯效开关/亮度。
- `docs/remaining-work.md:101` - **A 类 · 与原厂共存 / 低权限**（小爱 LX06 这类）：无 root、固件只读、不能改原厂；
- `docs/remaining-work.md:109` - 原则：机型差异全部收在 adapter 层，控制面/音频面不为某个品牌加分支；现有 LX06 实现作为 A 类样板。
- `docs/remaining-work.md:139` Dot2（B 类，正在另一个对话里开发），现有 LX06（A 类）作为样板。
- `docs/roadmap-0.2-execution.md:4` > 完成 LX06 插件化与第二个插头的标准化验证；体验类打磨留到 0.2 之后（P4）。
- `docs/roadmap-0.2-execution.md:10` P2.2 UI 向导 ──► P2.3 真·陌生人冷装 ──► P3.1 LX06 抽包 ──► 0.2.0
- `docs/roadmap-0.2-execution.md:23` | P2.3 | 安装向导 UI + 真·陌生人冷装（全新目录、公开命令、向导填 key、自动下模型、内置 LX06） | 从零到第一句 ≤30 min，全程只按文档 | UI 智能体 + 我部署 |
- `docs/roadmap-0.2-execution.md:34` | P3.1 | LX06 抽成 `rui-plugin-xiaoai`（manifest + adapter 包）；核心留 mock + loader；内置参考插头默认可用 | LX06 真机全链路不回归；核心无品牌分支 | P2.3 |
- `docs/roadmap-0.2-execution.md:54` - **LX06 抽包**：等 P2.3 通过后，独立仓库/包 + 核心 loader 调整（大改，单独窗口）
- `docs/roadmap-0.2-execution.md:60` - **0.2.0**：LX06 抽包 + 第二台设备 + 安全 + 文档/Release
- `docs/roadmap-0.6.md:1` # rui 0.6.0 路线图：多音箱完成 + 小爱剥离
- `docs/roadmap-0.6.md:3` > 目标（9 哥 定义）：**多音箱系统搭建完毕，小爱音箱从 rui 内核剥离干净。**
- `docs/roadmap-0.6.md:9` 1. **内核零设备代码**：src/rui 下不再有 xiaoai 专属实现；grep xiaoai 只剩「兼容别名 + 文档」两处白名单。
- `docs/roadmap-0.6.md:10` 2. **LX06 是 plug**：plugs/xiaoai 走与 dot2 相同的 rui-device/v1 manifest + RUI_ADAPTER_PLUGINS 加载；现有安装 adapter: xiaoai 零迁移。
- `docs/roadmap-0.6.md:11` 3. **中性默认**：核心默认 capture 16000/1ch/0ref、playback 16000、ack earcon；LX06 的 8ch/6mic/2ref 由它自己的 manifest 的 audio_defaults 声明。
- `docs/roadmap-0.6.md:13` 5. **真机验收**：LX06 + 2×Dot2 共存，各自唤醒、同词单 turn、barge 归属、故障隔离、回滚，全部通过。
- `docs/roadmap-0.6.md:29` - 兼容 shim：没有 audio_defaults 的旧 plug 用中性默认；xiaoai 的 manifest 声明 8ch/6mic/2ref/16000。
- `docs/roadmap-0.6.md:30` - 判据：现有 config v3 原样启动；LX06 链路单测全过；新字段 schema/catalog/doctor 测试；plug-test 旧报告不回归。
- `docs/roadmap-0.6.md:32` ### M2 — 小爱 plug 化（rui，3–4 天，依赖 M1）✅ 已完成（1fa96bd；生产镜像两个 plug 并列）
- `docs/roadmap-0.6.md:34` - `src/rui/device/xiaoai/` → `plugs/xiaoai/`（镜像自带，manifest 加载），`adapter: xiaoai` 保持不变。
- `docs/roadmap-0.6.md:36` - 判据：`git grep -l xiaoai src/` 只剩白名单；全量测试；旧 config 安装零迁移；镜像内 plug 列表含 xiaoai 与 dot2。
- `docs/roadmap-0.6.md:47` - .34 金丝雀 2 天（合成客户端 + 真 Dot2），生产灰度（先 0.6.0 一台 LX06，再加 Dot2），最后 LX06+2×Dot2 真机验收。
- `docs/roadmap-0.6.md:55` M1 (契约/默认) ──┬─┴─> M2 (小爱 plug 化)
- `docs/roadmap-0.6.md:71` | LX06 回归（plug 化） | 保留兼容别名一个 minor；先 .34 金丝雀再生产；旧 config 零迁移测试 |
- `docs/runbook.md:71` docker compose logs --since 60m rui | grep -E "xiaoai: speaker (connected|disconnected)"
- `docs/runbook.md:176` | 音箱换 IP / 重连风暴 | 新连接替换旧连接；旧连接被 close(1012) | ✅ 是 | 看 `xiaoai: speaker connected` 日志确认 |
- `docs/runbook.md:194` | 音箱不出声、不响应唤醒 | `/api/v1/devices`（`connected`）、`docker compose logs \| grep xiaoai`、`ss -ltnp \| grep 4412` | token 不一致（`.env` 的 `RUI_DEVICE_TOKEN` 与音箱 client 不同）→ 改一致后重启；4412 被旧进程占 → 停旧的；音箱 client 没指向 NAS IP |
- `docs/socket-plug-audit.md:81` - `docs/socket-plug-v1.md:39` — "RUI 0–100，adapter 映射（LX06 为 0–255）".
- `docs/socket-plug-audit.md:313` #### P1-6. Version ranges conflict; `rui_compat` is not the only gate; builtin LX06 advertises incompatibility with 0.3.2
- `docs/socket-plug-audit.md:317` - `src/rui/plugins/builtin.py:115` — builtin `xiaoai-lx06` says `rui_compat: ">=0.2.0 <0.3.0"`,
- `docs/socket-plug-audit.md:320` - `plugins/rui-plugin-xiaoai/plugin.yaml:5` — reference package says `>=0.2 <0.4`.
- `docs/socket-plug-audit.md:369` `/control` + `/data`; LX06 is plain ws + query token.
- `docs/socket-plug-audit.md:388` at least `protocol.kind`); if no block, keep the current LX06 default. Add `wss` to the
- `docs/socket-plug-audit.md:439` - examples: `examples/plugin-example` is inbound v1; `plugins/rui-plugin-xiaoai` is
- `docs/socket-plug-audit.md:595` - `docs/install-wizard-design.md:376` — "至少有 xiaoai.yaml、mock.yaml、dot2.yaml"; there is
- `docs/socket-plug-audit.md:596` no `mock` builtin manifest (`src/rui/plugins/builtin.py` only has LX06), and no
- `docs/socket-plug-audit.md:609` #### P2-8. Fairness / defaults are LX06-shaped
- `docs/socket-plug-audit.md:611` - `src/rui/config/schema.py:175` — `DeviceConfig.adapter` defaults to `"xiaoai"`; a device
- `docs/socket-plug-audit.md:612` added without an adapter becomes an LX06.
- `docs/socket-plug-audit.md:614` 2 ref / S32 with LX06 shifts; a third-party adapter can ignore it (the plane uses the
- `docs/socket-plug-audit.md:619` device; Q2 says 4412 is "not reserved" for LX06, but it is the implicit default.
- `docs/socket-plug-audit.md:620` - `src/rui/plugins/builtin.py:35-70` — only LX06 is builtin; the only `available` plug on
- `docs/socket-plug-audit.md:621` a fresh install is LX06. The dot2 path needs an operator env var and a self-declared
- `docs/socket-plug-audit.md:623` - `docs/socket-plug-v1.md:51` — the named "LX06 exception" is a special case in the
- `docs/socket-plug-audit.md:626` as LX06-specific. Remove the brand and state the generic rule.
- `docs/socket-plug-audit.md:628` the LX06 AEC path; dot2's device-AEC path is reported as missing (P1-3).
- `docs/socket-plug-audit.md:666` that omits it inherits the LX06 port even if it is not an LX06 (P2-8).
- `docs/socket-plug-audit.md:711` | Q4: closed LX06 unchanged; open devices can fall back | same (`v1:267`, `:353`) | LX06 probe has no DEVICE_WAKE; KWS unchanged (`xiaoai/adapter.py:121-129`; `process.py:645-663`) | **Consistent** |
- `docs/socket-plug-audit.md:834` ### LX06 / v0
- `docs/socket-plug-audit.md:836` - **Good:** LX06 builtin manifest is v0 (`src/rui/plugins/builtin.py:35-70`), probe has no
- `docs/socket-plug-audit.md:837` `DEVICE_WAKE` (`src/rui/device/xiaoai/adapter.py:121-129`), no `drain_wakes()`, and
- `docs/socket-plug-audit.md:843` (`plugins/rui-plugin-xiaoai/plugin.yaml:5`), the spec says `>=0.2 <0.4` (`v1:325`), and
- `docs/socket-plug-audit.md:891` The core code has no `if xiaoai`/`if dot2` branch, which is good. But the *contract and
- `docs/socket-plug-audit.md:894` 1. **Installability.** LX06 is builtin and `available`; a third-party plug is
- `docs/socket-plug-audit.md:898` 2. **Barge model.** `missing_for_barge_in()` models only the LX06 reference+playback path;
- `docs/socket-plug-audit.md:900` 3. **Defaults.** `DeviceConfig.adapter` defaults to `xiaoai` (`schema.py:175`);
- `docs/socket-plug-audit.md:902` the LX06 layout (`schema.py:212-221`); the example/plug docs use `listen_port: 4412`
- `docs/socket-plug-audit.md:903` (`v1:88`). None blocks dot2, but a fresh install is LX06-shaped and the third-party
- `docs/socket-plug-audit.md:905` 4. **Normative text names LX06** in §2.5 ("LX06 例外") and §6.5 (reference plug); the
- `docs/socket-plug-audit.md:911` 6. **TLS/wss** is required by dot2 but not expressible; LX06's plain ws is the only
- `docs/socket-plug-audit.md:920` (e) a second built-in/open example, and (f) removing the LX06-specific default from the
- `docs/socket-plug-audit.md:1022` > - `4412` 不是保留给 LX06，但它是所有 inbound 设备的默认值
- `docs/socket-plug-impl-audit.md:14` `plugins/rui-plugin-xiaoai/`, `examples/plugin-example/`,
- `docs/socket-plug-impl-audit.md:16` plus the LX06 adapter and the CI/security workflows.
- `docs/socket-plug-impl-audit.md:71` `/health` exposes only names/counts (`src/rui/web/api/app.py:2047-2050`). The LX06
- `docs/socket-plug-impl-audit.md:72` adapter logs the peer IP on connect/reject (`src/rui/device/xiaoai/adapter.py:332,341`),
- `docs/socket-plug-impl-audit.md:86` (`_apply`), `:199-237`; builders at `plugins/rui-plugin-xiaoai/rui_plugin_xiaoai/adapter.py:38`
- `docs/socket-plug-impl-audit.md:101` PYTHONPATH=plugins/rui-plugin-xiaoai \
- `docs/socket-plug-impl-audit.md:102` RUI_ADAPTER_PLUGINS=lxref=rui_plugin_xiaoai.adapter:build \
- `docs/socket-plug-impl-audit.md:104` # ["... factory returned XiaoaiAdapter, expected a builder ..."], registered == {}
- `docs/socket-plug-impl-audit.md:179` that fails once is never retried. The LX06 is especially exposed because its
- `docs/socket-plug-impl-audit.md:180` `frames()` waits forever with a 1 s timeout (`src/rui/device/xiaoai/adapter.py:169-176`)
- `docs/socket-plug-impl-audit.md:318` ### P1-8 — LX06 websocket accepts unbounded frames (`max_size=None`) and decodes them
- `docs/socket-plug-impl-audit.md:320` **Where:** `src/rui/device/xiaoai/adapter.py:150-157` (`serve(..., max_size=None)`),
- `docs/socket-plug-impl-audit.md:322` `src/rui/device/xiaoai/protocol.py:82-118` (`decode_stream`).
- `docs/socket-plug-impl-audit.md:421` `src/rui/device/xiaoai/adapter.py:120-122`.
- `docs/socket-plug-impl-audit.md:424` Example: a v0 LX06 manifest (no `device_wake`) + an env adapter whose probe advertises
- `docs/socket-plug-impl-audit.md:426` the builtin LX06 manifest declares `volume`/`keys` that its probe does not, and its
- `docs/socket-plug-impl-audit.md:429` **Repro:** device with builtin `adapter: xiaoai` and a probe reporting
- `docs/socket-plug-impl-audit.md:462` (`DeviceServices.read_volume`), `src/rui/device/xiaoai/adapter.py:285-289`,
- `docs/socket-plug-impl-audit.md:507` `_open_adapter()` fails (e.g. LX06 port already bound), the `finally` still runs
- `docs/socket-plug-impl-audit.md:522` ### P2-8 — Unbounded vendor text / malformed device JSON can kill the control-plane reader or the LX06 connection
- `docs/socket-plug-impl-audit.md:524` **Where:** `src/rui/device/xiaoai/protocol.py:178-220`; `src/rui/device/xiaoai/adapter.py:380-430`;
- `docs/socket-plug-impl-audit.md:531` (`results` not a list, `header` a string) raise inside `_on_text`; the LX06 handler
- `docs/socket-plug-impl-audit.md:535` LX06 disconnect.
- `docs/socket-plug-impl-audit.md:571` runs per request. For inbound LX06 this is only a probe object, but the hook is public.
- `docs/socket-plug-impl-audit.md:600` `src/rui/device/xiaoai/adapter.py:65` (both subclass the Protocol).
- `docs/socket-plug-impl-audit.md:621` means `RUI_ADAPTER_PLUGINS=xiaoai=...` silently replaces the builtin with one warning
- `docs/socket-plug-impl-audit.md:625` **Repro:** `RUI_ADAPTER_PLUGINS=xiaoai=rui.device.mock:build` → `_BUILDERS["xiaoai"]`
- `docs/socket-plug-impl-audit.md:707` hook); `tests/test_xiaoai_adapter.py`/`test_xiaoai_e2e.py` (no oversized frame, close
- `docs/socket-plug-impl-audit.md:767` - LX06 token is fail-closed, compared with `hmac.compare_digest`, and
- `docs/socket-plug-impl-audit.md:768` `allow_no_token` is explicit (`xiaoai/adapter.py:135-158,372-378`).
- `docs/socket-plug-impl-audit.md:778` 3. P1-8/P2-8 (LX06 input bounds) and P1-9 (entry-point guard).
- `docs/socket-plug-impl-audit.md:790` PYTHONPATH=plugins/rui-plugin-xiaoai RUI_ADAPTER_PLUGINS=lxref=rui_plugin_xiaoai.adapter:build \
- `docs/socket-plug-v1.md:15` **v1 保证**：接口在 0.3.x 内稳定；只靠 manifest 即可被理解；能力缺失即降级、不假装；`device_wake` 是一等事件但只是建议输入；v0 / LX06 行为不变；有契约测试工具可验收。**核心不出现品牌分支**（不得 `if xiaoai` / `if dot2`）。
- `docs/socket-plug-v1.md:40` | `device_wake` | 设备本地检测 RUI 唤醒词，经 `drain_wakes()` 上报 `DeviceWake` | RUI KWS 路径完全不变（LX06） | 要求 `wake.source: device`；与 vendor wake 分离 |
- `docs/socket-plug-v1.md:49` 3. `led`/`volume`/`keys` 由 manifest 决定、`DeviceServices` 的实际可调用性佐证，不要求出现在 `probe()`；服务抛 `NotImplementedError` 时按运行时降级。LX06 的 v0 probe 未列 `volume`/`keys`，这是合法的，v1 不得用简单交集误降级。
- `docs/socket-plug-v1.md:146` LX06 的 8 ch / 6 mic / 2 ref / 48 kHz 由它自己的 manifest 声明
- `docs/socket-plug-v1.md:147` （`plugs/xiaoai/devices/lx06.yaml`，镜像内为 `/opt/rui-plugins/xiaoai/devices/lx06.yaml`），
- `docs/socket-plug-v1.md:156` - LX06 manifest 自 0.6 起是 `rui-device/v1`（唤醒仍走 RUI KWS：省略 `wake` 即
- `docs/socket-plug-v1.md:157` `source: rui`），随镜像放在 `plugs/xiaoai/`；没有 `audio_defaults` 的旧 plug
- `docs/socket-plug-v1.md:214` | `DeviceServices.set_volume()` / `read_volume()` | **0–255** | 设备混音器；实现必须拒绝越界而不是静默 clamp。`0` 在范围内，但 LX06 把 0 视为“保留给厂商”并拒绝写入；核心不会发送 0（config `volume=0` = 不托管） |
- `docs/socket-plug-v1.md:217` 换算只允许通过 `device/contract.py` 的 `percent_to_device_volume()` / `device_volume_to_percent()`；adapter 不应各自复制 magic number（LX06 混音器就是 0–255）。
- `docs/socket-plug-v1.md:283` RUI_ADAPTER_PLUGINS=dot2=my_plugin.adapter:build,lx06x=other:build
- `docs/socket-plug-v1.md:310` - **内置名字不可覆盖**：`xiaoai`/`mock` 等 image 自带 adapter 不能被 env 或 entry point 替换；要不同行为请注册新名字并改 `devices[].adapter`。env 同名项之间后者覆盖前者（运维方显式顺序），entry point 永远 `allow_override=False` 且不覆盖 env/builtin。
- `docs/socket-plug-v1.md:344` | 未声明 `device_wake` | RUI KWS 完全不变（LX06） |
- `docs/socket-plug-v1.md:383` | Playback gap harness | 模拟 issue #1 的“首块后 ~400 ms 才到下一批”；断言 `playback_gap_count` 不增长、`max_gap_ms <100` | 参数可复现；LX06 基线不回归 |
- `docs/socket-plug-v1.md:391` 建议值（待 §8 Q7 确认）：manifest validator 全绿；注入 20 次空闲唤醒 + 有语音 → ≥19 次形成 turn，静音 20 次 → 0 turn；播放中 `barge=true` 20 次 → `silence_after_ms ≤300 ms`；issue #1 的 400 ms 双批源 → `max_gap_ms <100`、`underruns=0`，额外延迟短回复 ≤150 ms、长回复可配 ≤300 ms；无 `device_wake` 的 LX06 行为不变；A 类完成厂商让位/接管，B 类完成回滚演练。
- `docs/socket-plug-v1.md:397` ### 6.5 镜像自带插头：LX06（`plugs/xiaoai/`）
- `docs/socket-plug-v1.md:399` 0.6 M2（`docs/roadmap-0.6.md`）把 `src/rui/device/xiaoai/` 搬成与 dot2 同构的插头：核心没有 LX06 分支，镜像把 `plugs/` 复制到 `/opt/rui-plugins`，manifest loader 扫描该目录，registry 再从 manifest 的 `runtime.entrypoint` 注册 adapter。它**是**生产运行时路径，不再是参考包。
- `docs/socket-plug-v1.md:403` | `plugin.yaml` | id `xiaoai-lx06`，trust `builtin`，`rui_compat: ">=0.3.0 <0.7.0"` | §3.1 / §3.2 |
- `docs/socket-plug-v1.md:404` | `devices/lx06.yaml` | `rui-device/v1`，id `lx06`，class A，inbound，`ws`，in-process，能力 `mic_array` / `reference` / `playback` / `led` / `keys` / `volume`，`audio_defaults` 48000/8 ch/6 mic/2 ref/32 bit/>>12/>>16 + `ack_echo_ignore_ms: 1200`，参数 `host` / `room` / `wake_words` | §3、§7 |
- `docs/socket-plug-v1.md:405` | `xiaoai_plug/` | adapter / wire protocol / LED ring / mixer services；`build(dev, cfg)`，`kit_live_device_required = True` | §4.1 |
- `docs/socket-plug-v1.md:406` | `wake` | 无；v1 默认 `source: rui`，LX06 继续走 RUI KWS | §2、§7 |
- `docs/socket-plug-v1.md:409` python -m rui plug-test plugs/xiaoai
- `docs/socket-plug-v1.md:410` python -m rui plug-test plugs/xiaoai \
- `docs/socket-plug-v1.md:411` --adapter xiaoai_plug.adapter:build --json
- `docs/socket-plug-v1.md:414` 预期 16 PASS / 0 FAIL / 0 WARN / 5 SKIP（0.5.3 + 0.6 M2，同步记录在 `plugs/xiaoai/README.md`）：`manifest.*`、`adapter.import/construct/methods`、`device.ready/probe_layout`、两个 `poll.*`、`lifecycle.*` 通过；`device.open`、`capture.frames`、`capture.cadence`、`playback.sink` 记为 **SKIP**，因为 LX06 是 inbound，必须等 open-xiaoai client 拨入才能跑帧/播放；`wake.inject` 也 SKIP（RUI KWS，没有 device_wake）。这些 SKIP 由 adapter 的 kit-only 提示 `kit_live_device_required` 标明（`docs/plug-test-kit.md`），不改变生产行为、不伪造设备，也不把 SKIP 记成 PASS。
- `docs/socket-plug-v1.md:417` > `device.probe_layout` 与 `device.device_wake_effective`。LX06 走 RUI KWS 且
- `docs/socket-plug-v1.md:420` 迁移：`adapter: xiaoai` 在 0.6.x 仍可用（`src/rui/adapter_aliases.py` 的单 minor
- `docs/socket-plug-v1.md:421` 别名），新配置写 manifest 的机型 id `adapter: lx06`。旧 config 零迁移即启动。
- `docs/socket-plug-v1.md:434` | 音频默认 | 无（核心写死 LX06） | 新增可选 `audio_defaults`（0.6）：`capture`/`playback`/`ack_echo_ignore_ms` |
- `docs/socket-plug-v1.md:437` **兼容规则**：loader 接受 v0，按 `wake.source=rui`、`barge_via_device=false` 处理；v0 不得声明 `wake`/`device_wake`/`barge_via_device`/`model`/`audio_defaults`（否则报错，不静默升级）。0.6 起 LX06 的 manifest（`plugs/xiaoai`，镜像内 `/opt/rui-plugins/xiaoai`）升为 `rui-device/v1` 以声明 `audio_defaults`（唤醒仍走 RUI KWS，省略 `wake` 即 `source: rui`）；LX06 adapter 不实现 `drain_wakes()`，KWS 路径不变。v0 兼容样本改由 `examples/plugin-example` 与测试 fixture 覆盖。v0 + 新字段、v1 + 未知字段、`rui_compat` 不匹配都报文件/字段。`devices[].params` 仍是值字典，`params.plug_id` 继续作为 adapter→plug 兜底（`src/rui/plugins/resolve.py`）。
- `docs/socket-plug-v1.md:449` - 镜像 LX06 插头 `plugs/xiaoai`：v1 矩阵 `rui_compat: ">=0.3.0 <0.7.0"`、`rui_min: "0.3.0"`。它由 `load_plugin()` 加载，兼容上下限由 loader 强制（0.6 M2 之前这是绕过 loader 的 in-code builtin，上限 `<0.7.0`）。
- `docs/socket-plug-v1.md:482` **弃用**：`rui-device/v0` 在 0.3.x–0.5.x 继续支持，最早 0.6.0 移除（0.5.0 保留 v0，因为当时的 LX06 参考插头仍是 v0；0.6 M2 起自带插头已是 v1）。0.3.2 起 `load_plugin()` 每个 v0 manifest 路径每进程记一次 warning（`src/rui/plugins/loader.py:_warn_v0_once`），避免每请求刷屏；`native_kws` 自 v1 弃用（v1 manifest 直接拒绝）；`streaming_asr` 保留为未定义值；`VendorSignalMsg`/`drain_signals()`/厂商让路窗口不弃用、不改语义；任何字段移除都“先警告、后 minor、再移除”，不得在 patch 移除。
- `docs/socket-plug-v1.md:484` **提取已完成（0.6 M2，取代旧的 0.4 计划）**：`src/rui/device/xiaoai/` 的实现已移入 `plugs/xiaoai/`，核心 registry 不再有任何设备适配器表项；`plugins/builtin.py` 只扫描镜像插头目录（`/opt/rui-plugins`）并交给同一个 manifest loader，`src/rui/plugins/builtin.py` 里没有任何机型数据；adapter entrypoint 为 `xiaoai_plug.adapter:build`，manifest 的 device id 是 `lx06`，而旧配置的 `devices[].adapter: xiaoai` 通过 `src/rui/adapter_aliases.py` 的单 minor 别名继续工作（计划 0.7.0 移除）。本切片不删除 v0 schema 支持。
- `docs/socket-plug-v1.md:488` 1. ✅ manifest 加载：`plugs/xiaoai/{plugin.yaml,devices/lx06.yaml}`，镜像内 `/opt/rui-plugins/xiaoai`；
- `docs/socket-plug-v1.md:490` 3. ✅ 名称别名：`xiaoai` → `lx06`，一个 minor，旧 config 零迁移。
- `docs/socket-plug-v1.md:502` | `tests/` | manifest v1、wake injection、playback gap、LX06 回归 |
- `docs/socket-plug-v1.md:607` | Q2 | 多设备与端口 | 每设备 `devices[].listen_port` 已支持；manifest `runtime.listen_port`/`token_env` 在设备配置未显式设置时作为缺省值应用（`build_adapters()`），配置值优先。**4412 不保留给 LX06**，Dot2 用 8870/wss 不冲突；0.3 仍是**一个音频面实例驱动一台设备**，多设备在线显式 warning，不做假仲裁 | 每个 enabled 设备独立音频面 + 独立端口；多设备唤醒仲裁 |
- `docs/socket-plug-v1.md:640` | S14 | 移除规范正文的品牌例外；LX06 仅作参考插头；默认端口/参数不再表述为品牌特权 | 文档 |
- `docs/socket-plug.md:5` > 相关：`ROADMAP` §3 P2/P3、`remaining-work.md` §I/§J/§K、`open-xiaoai-client.md`、`device-management-research.md`（EchoMuse 调研）。
- `docs/socket-plug.md:13` | **A 类音箱** | 与原厂共存、低权限：不 root、不改固件；设备上跑厂商允许的 client（如 LX06 + open-xiaoai），必须处理厂家让位/接管。 |
- `docs/socket-plug.md:20` 2. **核心无品牌分支**：机型差异全部在插头/adapter；控制面、音频面、控制台不写 `if xiaoai`。
- `docs/socket-plug.md:86` name: "open-xiaoai client"
- `docs/socket-plug.md:87` source: "https://github.com/coderzc/open-xiaoai"
- `docs/socket-plug.md:122` notes: "A 类需要 open-xiaoai client ≥ v0.x；固件 1.94.x 验证"
- `docs/socket-plug.md:157` | 适用 | `builtin`/`trusted` 插头；LX06 参考插头 | 第三方插头默认 |
- `docs/socket-plug.md:172` **A 类 · 小爱音箱 Pro（LX06，原厂共存）**
- `docs/socket-plug.md:173` 1. 音箱：LX06（固件版本按 `open-xiaoai`/`xiaoai-protocol.md` 要求），开启 SSH；
- `docs/socket-plug.md:174` 2. 安装/刷 `open-xiaoai` client（上游 https://github.com/coderzc/open-xiaoai；我们验证的 fork/最低版本写在插头 manifest）；
- `docs/socket-plug.md:177` 5. 装 RUI → 向导选 LX06 插头 → 生成 ws URL+token → 填进 client；
- `docs/vendor.md:1` # Yielding to 小爱
- `docs/vendor.md:3` The speaker still runs its own assistant. "小爱同学" wakes the vendor, it answers, and our
- `docs/vendor.md:22` client (fork)  --{"Event": {"event": "instruction", ...}}-->  xiaoai adapter
- `docs/vendor.md:41` 2. say **小爱同学，今天天气怎么样** once;
- `docs/vendor.md:42` 3. expected: one answer (the vendor's), a `xiaoai: vendor wake (...), we yield` line, and
- `docs/vendor.md:52` First real-device run of the yield path, LX06 on `:4412`, restored to `:4399` afterwards.
- `docs/vendor.md:55` 08:33:57  xiaoai: vendor wake ('今天天气怎么样'), we yield
- `docs/vendor.md:75` ## Takeover: "你好小瑞" while 小爱 is answering (closed 2026-10-22 19:30)
- `docs/vendor.md:77` The scenario 9哥 tested: say 小爱同学 and ask a question, 小爱 starts answering, then say
- `docs/vendor.md:78` 你好小瑞. Expected: one answer at a time. 小爱's dialog is left behind and the line is ours.
- `docs/vendor.md:86` | control plane | text backstop: a transcript containing 小爱同学 yields too, for client builds that miss `StartCapture` | `b3da6ec` (call fixed) |
- `docs/vendor.md:92` 19:30:15.279  xiaoai: vendor wake ('今天星期几'), we yield
- `docs/vendor.md:93` 19:30:15.569  event playing Playing          ← 小爱 answers
- `docs/vendor.md:109` | V1 | ~~We do not actively stop 小爱 when 你好小瑞 fires~~ **Implemented `3b4648e`, device re-verify pending**: the adapter tracks the vendor player from `playing` events; a wake while it plays fires the guarded mphelper ubus pause (`vendor pause (wake takeover): before=1 after=2` is the log line to look for). The 20:09 re-test was voided by an ops outage (server relaunched without keys), not by this code | 19:30 log: vendor `Idle` 1.5 s after our wake; 19:54: it talked 11 s more | `vendor_stop_player()` `assistant_v3.py:2248`: `ubus call mediaplayer player_play_operation '{"action":"pause","media":"common"}'`, only when `mphelper mute_stat` is 1/3 (`player_stop` does not exist, rc=3). The heavier route is `abort_xiaoai()` (client `speaker.rs:143`, `/etc/init.d/mico_aivs_lab restart`) |
- `docs/vendor.md:111` | V3 | While the vendor player is playing, our gate still listens and can transcribe 小爱's voice as a request | 18:56:55 our turn transcribed `到17摄氏度，南方向。` (the vendor's weather answer); 19:54:06 again (`摄氏度到17摄氏度。`), and it became a phantom turn while 小爱 was still talking | Old build gated the mic for the whole vendor playback: `assistant_v3.py:3113-3116` (90 s freshness). rui keeps its KWS live there on purpose (better takeover); V1's pause plus the cue hold is expected to cover it - verify with the V1 re-test, keep the full gate as fallback if a leak shows |
- `docs/xiaoai-protocol.md:1` # The LX06 wire protocol
- `docs/xiaoai-protocol.md:4` read off the client that actually runs on the speaker (open-xiaoai fork) and the
- `docs/xiaoai-protocol.md:56` ## Measured on the speaker (2026-10-21, LX06 1.94.13)

</details>

## 附录 B：修复前后原文对照（74 条替换）

多行片段用 `⏎` 表示换行；行号是修改前 `HEAD` 中的行号。

| # | file:line（修前） | 修前 | 修后 |
|---|---|---|---|
| 1 | `src/rui/audio/ack_render.py:3` | so it cannot itself cost latency. On the LX06 a wake | so it cannot itself cost latency. On the reference speaker a wake |
| 2 | `src/rui/audio/arbiter.py:240` | arbiter.set_state(turn_open=True, owner_device="lx06", playing={"lx06"}) | arbiter.set_state(turn_open=True, owner_device="living", playing={"living"}) |
| 3 | `src/rui/audio/chime.py:4` | acknowledgement keeps the LX06 microphone muted | acknowledgement keeps the reference speaker's microphone muted |
| 4 | `src/rui/audio/decimate.py:3` | The LX06 hands over 48 kHz | A reference array speaker hands over 48 kHz |
| 5 | `src/rui/audio/probe.py:93` | Prefer the object's own cached ``info`` (the LX06 builds one in ``__init__`` and its ⏎ probe simply returns it); otherwise return an honest empty layout with the configured | Prefer the object's own cached ``info`` (an adapter that builds one in ``__init__`` ⏎ and whose probe simply returns it); otherwise return an honest empty layout with the configured |
| 6 | `src/rui/audio/process.py:5` | so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32 | so an array speaker (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32 |
| 7 | `src/rui/audio/process.py:723` | than an LX06 needs; reading the global value here is what let its own cue open a | than the reference speaker needs; reading the global value here is what let its own cue open a |
| 8 | `src/rui/audio/process.py:1811` | # wake never had to stop 小爱; ours hears 你好小瑞 mid-answer, and taking the | # wake never had to stop the vendor assistant; ours hears 你好小瑞 mid-answer, and taking the |
| 9 | `src/rui/audio/protocol.py:296` | Not audio and not ours: the control plane decides whether to stay quiet. 小爱同学 ⏎ waking the vendor's assistant used to make both assistants answer the same sentence. | Not audio and not ours: the control plane decides whether to stay quiet. The ⏎ vendor's wake phrase waking its assistant used to make both answer the same sentence. |
| 10 | `src/rui/config/catalog.py:108` | "从音箱收到的原始格式。这些是照 LX06 实测写的（48000 Hz / 8 通道 / 32 bit，" | "从音箱收到的原始格式。这些数值是按参考机型实测的（48000 Hz / 8 通道 / 32 bit，" |
| 11 | `src/rui/config/catalog.py:110` | "The raw format the speaker sends. Measured on LX06 (48000 Hz / 8 ch / 32 bit, " | "The raw format the speaker sends. Measured on the reference model (48000 Hz / 8 ch / 32 bit, " |
| 12 | `src/rui/config/catalog.py:118` | "推给音箱的音频格式和控速。LX06 客户端只收 16 kHz 单声道；credit_window 是在途上限" | "推给音箱的音频格式和控速。参考机型的客户端只收 16 kHz 单声道；credit_window 是在途上限" |
| 13 | `src/rui/config/catalog.py:120` | "Format pushed to the speaker and write pacing. The LX06 client takes 16 kHz mono " | "Format pushed to the speaker and write pacing. The reference client takes 16 kHz mono " |
| 14 | `src/rui/config/catalog.py:568` | _t("说「小爱同学」后我们安静多久：避免两个助手同时答。调大更保守，" | _t("设备自带助手应答后我们安静多久：避免两个助手同时答。调大更保守，" |
| 15 | `src/rui/config/catalog.py:580` | "音箱实际输出的采样率。LX06 是 48000；填错会变调或直接没声音。" | "音箱实际输出的采样率。参考机型为 48000 Hz（生产模板会让客户端降到 16000 Hz 以省带宽）；填错会变调或直接没声音。" |
| 16 | `src/rui/config/catalog.py:581` | "The rate the speaker actually sends. LX06 is 48000; a wrong value pitch- " ⏎ "shifts or silences it." | "The rate the speaker actually sends. The reference model is 48000 Hz (the production " ⏎ "template asks its client for 16000 Hz to save bandwidth); a wrong value pitch-shifts or silences it." |
| 17 | `src/rui/config/catalog.py:584` | "采集总通道数 = 麦通道 + 参考通道。LX06 是 8。填错 DSP 会读错帧。" | "采集总通道数 = 麦克风通道 + 参考通道。参考机型为 8 通道。填错会让 DSP 读错帧。" |
| 18 | `src/rui/config/catalog.py:585` | "Total capture channels = mic + reference. LX06 is 8; a wrong value misreads " | "Total capture channels = mic + reference. The reference model has 8 channels; a wrong value misreads " |
| 19 | `src/rui/config/catalog.py:588` | "前几路是麦。LX06 是 6 路；换 4 麦音箱时改这里。" | "前几路是麦克风。参考机型有 6 路；换 4 麦音箱时改这里。" |
| 20 | `src/rui/config/catalog.py:589` | "The first N channels are mics. LX06 has 6; change it for a 4-mic speaker." | "The first N channels are mics. The reference model has 6; change it for a 4-mic speaker." |
| 21 | `src/rui/config/catalog.py:592` | "最后几路是播放参考（AEC 用）。LX06 是 2；删掉参考会失去回声消除。" | "最后几路是播放参考（供 AEC 使用）。参考机型有 2 路；删掉参考会失去回声消除。" |
| 22 | `src/rui/config/catalog.py:593` | "The last N channels are the playback reference for AEC. LX06 has 2; removing " | "The last N channels are the playback reference for AEC. The reference model has 2; removing " |
| 23 | `src/rui/config/catalog.py:596` | "设备 ALSA 的位深。LX06 是 32；填错电平/削顶都会异常。" | "设备 ALSA 的位深。参考机型为 32 bit；填错会导致电平异常或削顶。" |
| 24 | `src/rui/config/catalog.py:597` | "The device's ALSA bit depth. LX06 is 32; a wrong value breaks levels/clipping." | "The device's ALSA bit depth. The reference model is 32 bit; a wrong value breaks levels or clipping." |
| 25 | `src/rui/config/catalog.py:605` | "参考通道的右移位数。参考满量程是麦的 32 倍，LX06 用 16；改错 AEC 效果骤降。" | "参考通道的右移位数。参考满量程是麦克风的 32 倍，参考机型用 16；改错会让 AEC 效果骤降。" |
| 26 | `src/rui/config/catalog.py:606` | "Right-shift for the reference. Its full scale is 32x the mic, so LX06 uses 16; " | "Right-shift for the reference. Its full scale is 32x the mic, so the reference model uses 16; " |
| 27 | `src/rui/config/catalog.py:646` | "回答结束后等多久重新开麦听下一句（毫秒）。1000 是 LX06 的实测回声尾；设备" | "回答结束后等多久重新开麦听下一句（毫秒）。1000 ms 是参考机型实测的回声尾；设备" |
| 28 | `src/rui/config/catalog.py:649` | "How long after playback drains before the mic reopens (ms). 1000 is the " ⏎ "LX06's measured echo tail; a device-AEC plug (RUI AEC off) may need 2500. " | "How long after playback drains before the mic reopens (ms). 1000 ms is the " ⏎ "reference speaker's measured echo tail; a device-AEC plug (RUI AEC off) may need 2500. " |
| 29 | `src/rui/config/catalog.py:698` | "推给音箱的采样率。LX06 客户端只接受 16000；填 48000 会被 adapter 拒绝、没声音。" | "推给音箱的采样率。参考机型的客户端只接受 16000 Hz；填 48000 会被 adapter 拒绝、没有声音。" |
| 30 | `src/rui/config/catalog.py:699` | "Rate pushed to the speaker. The LX06 client takes 16000 only; 48000 is rejected " | "Rate pushed to the speaker. The reference client takes 16000 Hz only; 48000 is rejected " |
| 31 | `src/rui/config/catalog.py:702` | "推给音箱的通道数。LX06 只收单声道；填 2 会被拒绝。" | "推给音箱的通道数。参考机型只接受单声道；填 2 会被拒绝。" |
| 32 | `src/rui/config/catalog.py:703` | "Channels pushed to the speaker. The LX06 takes mono only; 2 is rejected." | "Channels pushed to the speaker. The reference model takes mono only; 2 is rejected." |
| 33 | `src/rui/config/catalog.py:1494` | "前几个通道是麦克风（留空 = 继承全局；LX06 是 6）。改完重启音频面。" | "前几个通道是麦克风（留空 = 继承全局；参考机型有 6 路）。改完重启音频面。" |
| 34 | `src/rui/config/catalog.py:1495` | "Leading channels that are microphones (empty = inherit; LX06 is 6). " | "Leading channels that are microphones (empty = inherit; the reference model has 6). " |
| 35 | `src/rui/config/catalog.py:1508` | "采集样本的位深（16 或 32；留空 = 继承全局；LX06 是 32）。改完重启音频面。" | "采集样本的位深（16 或 32；留空 = 继承全局；参考机型为 32 bit）。改完重启音频面。" |
| 36 | `src/rui/config/catalog.py:1509` | "Capture sample width (16 or 32; empty = inherit; LX06 is 32). Restart " | "Capture sample width (16 or 32; empty = inherit; the reference model is 32 bit). Restart " |
| 37 | `src/rui/config/catalog.py:1514` | "把麦克风样本左移多少位到 32 bit（留空 = 继承全局；LX06 是 12）。" | "把麦克风样本左移多少位到 32 bit（留空 = 继承全局；参考机型为 12）。" |
| 38 | `src/rui/config/catalog.py:1516` | "Left shift applied to mic samples into 32-bit (empty = inherit; LX06 " ⏎ "is 12). A wrong value clips or goes quiet. Restart the plane." | "Left shift applied to mic samples into 32-bit (empty = inherit; the reference " ⏎ "model is 12). A wrong value clips or goes quiet. Restart the plane." |
| 39 | `src/rui/config/catalog.py:1521` | "把参考样本左移多少位到 32 bit（留空 = 继承全局；LX06 是 16）。" | "把参考样本左移多少位到 32 bit（留空 = 继承全局；参考机型为 16）。" |
| 40 | `src/rui/config/catalog.py:1523` | "Left shift applied to reference samples into 32-bit (empty = inherit; " ⏎ "LX06 is 16). A wrong value mis-scales the AEC reference. Restart." | "Left shift applied to reference samples into 32-bit (empty = inherit; " ⏎ "the reference model is 16). A wrong value mis-scales the AEC reference. Restart." |
| 41 | `src/rui/config/schema.py:201` | far longer than the 1200 ms the ⏎ # LX06 needs), but the value it overrides | far longer than the 1200 ms the ⏎ # reference speaker needs), but the value it overrides |
| 42 | `src/rui/config/schema.py:292` | (device default < global config < devices[].audio). The LX06, for instance, | (device default < global config < devices[].audio). A multi-mic speaker, for instance, |
| 43 | `src/rui/config/schema.py:302` | # A 16-bit neutral wire needs no scaling; a 32-bit device (the LX06) declares the | # A 16-bit neutral wire needs no scaling; a 32-bit device (a measured array) declares the |
| 44 | `src/rui/config/schema.py:357` | # for the next sentence. 1000 ms is the LX06's measured echo tail; a device-AEC | # for the next sentence. 1000 ms is the reference speaker's measured echo tail; a device-AEC |
| 45 | `src/rui/config/schema.py:376` | The capture/ASR/TTS side is 16 kHz, the LX06 client plays mono frames (the | The capture/ASR/TTS side is 16 kHz, the reference client plays mono frames (the |
| 46 | `src/rui/config/schema.py:387` | # reports. Measured on LX06: the report fires when a block enters aplay's stdin | # reports. Measured on a real speaker: the report fires when a block enters aplay's stdin |
| 47 | `src/rui/config/schema.py:394` | # to the device. Zero (default) keeps the LX06 path exactly as it was: its control | # to the device. Zero (default) keeps the reference path exactly as it was: its control |
| 48 | `src/rui/config/schema.py:413` | # v3: who runs the canceller. "controller" is the LX06 topology we measured | # v3: who runs the canceller. "controller" is the reference topology we measured |
| 49 | `src/rui/config/schema.py:427` | mutes the LX06 microphone for about 1.2 s plus its own length, so the cap is enforced | mutes the reference speaker's microphone for about 1.2 s plus its own length, so the cap is enforced |
| 50 | `src/rui/config/schema.py:552` | layout while an explicit value - including the LX06's 16 kHz capture override - | layout while an explicit value - including the production template's 16 kHz capture override - |
| 51 | `src/rui/config/schema.py:610` | residual echo far longer than the 1200 ms an LX06 needs), but the value it replaces | residual echo far longer than the 1200 ms the reference speaker needs), but the value it replaces |
| 52 | `src/rui/config/schema.py:1149` | # actually declares (an LX06 inherits 6 mic + 2 ref), not against the | # actually declares (a multi-mic plug inherits 6 mic + 2 ref), not against the |
| 53 | `src/rui/config/store.py:175` | next boot (an LX06 would come back as 1-channel audio after any console save). | next boot (a multi-channel speaker would come back as 1-channel audio after any console save). |
| 54 | `src/rui/config/template.yaml:43` | # Measured on LX06 1.94.13. Do not copy these numbers to another speaker; measure it. | # Measured on the reference speaker (firmware 1.94.13). Do not copy these numbers to ⏎ # another speaker; measure it. |
| 55 | `src/rui/config/template.yaml:68` | # The LX06 client plays mono frames; 16 kHz is the rate the whole pipeline runs at. | # The reference client plays mono frames; 16 kHz is the rate the whole pipeline runs at. |
| 56 | `src/rui/device/contract.py:10` | inbound - the device's client dials us (LX06: ws://<server>:<port>/?token=...). | inbound - the device's client dials us (ws://<server>:<port>/?token=...). |
| 57 | `src/rui/device/contract.py:52` | #: downgrade a working LX06. | #: downgrade a working speaker. |
| 58 | `src/rui/device/contract.py:74` | evidence and a v0 LX06 probe deliberately does not list them. | evidence and a v0 probe deliberately does not list them. |
| 59 | `src/rui/device/contract.py:296` | The reference channels travel inside the same frame as the microphones (LX06: ⏎ ch0..ch5 mics, ch6..ch7 reference), so a frame describes one layout for all of | The reference channels travel inside the same frame as the microphones (an array ⏎ speaker: ch0..ch5 mics, ch6..ch7 reference), so a frame describes one layout for all of |
| 60 | `src/rui/device/contract.py:333` | reports no VOLUME/LED capability; nothing above has to ask "is this a 小爱". | reports no VOLUME/LED capability; nothing above has to ask which model this is. |
| 61 | `src/rui/device/contract.py:348` | "leave it to the vendor" (the LX06's services rejects a 0 write); core never | "leave it to the vendor" (a vendor mixer may reject a 0 write); core never |
| 62 | `src/rui/device/contract.py:380` | #: ``__init__`` (the LX06 does). | #: ``__init__`` (an adapter may). |
| 63 | `src/rui/device/contract.py:392` | """Queue PCM for playback. Channels matter: LX06 plays 2 ch at 48 kHz.""" | """Queue PCM for playback. Channels matter: a device may play 2 ch at 48 kHz.""" |
| 64 | `src/rui/device/contract.py:436` | from an LX06, and RUI's own KWS path stays exactly as it was. Vendor wakes belong | from a KWS-only speaker, and RUI's own KWS path stays exactly as it was. Vendor wakes belong |
| 65 | `src/rui/device/mock.py:3` | Mirrors the LX06's real wire format - 48 kHz, 8 interleaved channels, 6 mics and 2 | Mirrors a real array speaker's wire format - 48 kHz, 8 interleaved channels, 6 mics and 2 |
| 66 | `src/rui/device/registry.py:161` | # Two layouts ship in the wild: <plug>/<package>/adapter.py (the LX06 image | # Two layouts ship in the wild: <plug>/<package>/adapter.py (an image plug |
| 67 | `src/rui/doctor/probes.py:40` | # until M1 measures it on the LX06; the doctor prints it so nobody mistakes it for data. | # until it is measured on real hardware; the doctor prints it so nobody mistakes it for data. |
| 68 | `src/rui/doctor/probes.py:412` | not the LX06's 8 ch / 48 kHz) or a device sets its own playback rate (the 0.5.3 D2 | not an array speaker's 8 ch / 48 kHz) or a device sets its own playback rate (the 0.5.3 D2 |
| 69 | `src/rui/doctor/probes.py:442` | The Dot 2 wire is 48 kHz mono; the LX06 plays 16 kHz mono, so a device without its | A downstream plug's wire is 48 kHz mono; the image plug plays 16 kHz mono, so a device without its |
| 70 | `src/rui/plugins/builtin.py:3` | 0.6 M2 (docs/roadmap-0.6.md): the LX06 manifest is no longer in code. The image | 0.6 M2 (docs/roadmap-0.6.md): device manifests are no longer in code. The image |
| 71 | `src/rui/plugins/contract.py:1579` | # DeviceInfo describes the capture layout (LX06: 8 channels). Playback is a | # DeviceInfo describes the capture layout (an array: 8 channels). Playback is a |
| 72 | `src/rui/plugins/loader.py:252` | builtin LX06 reference plug is still v0). Logging once avoids a warning flood | builtin reference plug was still v0). Logging once avoids a warning flood |
| 73 | `src/rui/plugins/models.py:97` | RUI = "rui" # RUI's own KWS (the LX06 path, unchanged) | RUI = "rui" # RUI's own KWS (the core path, unchanged) |
| 74 | `src/rui/session/vendor.py:3` | The LX06 still runs 小爱. Saying "小爱同学" wakes *it*, and it answers - which is fine ⏎ until our own wake word fires on the same audio and we answer too: two assistants, one ⏎ sentence, from the old project's log at 20:47 o… | A speaker may still run its vendor assistant. Its wake word wakes *it*, and it answers - ⏎ which is fine until our own wake word fires on the same audio and we answer too: two ⏎ assistants, one sentence, from the old pr… |

## 0.7.6 B1b 收口（2026-10-27）

### 1. 测试移植：桥仓 41 条

`rui-xiaoai-bridge/tests/` 新增/接收 5 个文件（语义用例从 core 原样移植，导入改为
`rui_xiaoai_bridge`）：`test_xiaoai_adapter.py`、`test_xiaoai_e2e.py`、
`test_xiaoai_led.py`、`test_xiaoai_limits.py`、`test_xiaoai_protocol.py`；加原有
catalog/import/manifest/manifest-compat/reconnect/residual 共 **44 passed**（2026-10-27 复测）。
`tests/conftest.py` 把 `src/` 设为 `RUI_PLUGINS_DIR`，让 core 的 registry 能发现桥 manifest。
被删 `test_xiaoai_plug.py` 的 8 条覆盖去向：

| 原测试名（`c713916^:tests/test_xiaoai_plug.py`） | 新位置 / 废弃原因 |
|---|---|
| `test_the_image_plug_is_a_v1_manifest_with_audio_defaults` | `rui-xiaoai-bridge/tests/test_manifest.py::test_manifest_loads_through_rui_loader` + `::test_audio_defaults_and_entrypoint_are_preserved` |
| `test_the_catalog_finds_the_image_plug_without_an_in_code_table` | `house-ear/tests/test_plugins.py::test_loads_a_valid_multi_device_plugin`（sample-plug 经通用 catalog/loader，无 in-code 表） |
| `test_the_old_adapter_name_is_a_one_minor_alias` | 语义变为迁移：`house-ear/tests/test_legacy_adapter_migration.py::test_legacy_spelling_is_rewritten_and_recorded`（旧 alias 本身已删） |
| `test_an_old_config_builds_the_same_adapter_without_editing` | 同上 `::test_legacy_spelling_is_rewritten_and_recorded`（新增 `build_adapters` 断言，改写后仍构建同一 adapter） |
| `test_the_adapter_builder_keeps_the_kit_hint_and_layout` | `rui-xiaoai-bridge/tests/test_import.py::test_build_needs_a_device_spec` + `test_manifest.py::test_device_contract_is_the_inbound_lx06_shape` |
| `test_the_adapter_maps_volume_to_the_device_services` | 废弃：core 不再持有 adapter；桥仓当前无 volume 映射单测（12c/task-14 补） |
| `test_the_plug_passes_the_contract_kit` | `rui-xiaoai-bridge/tests/test_import.py::test_package_imports_and_exports_the_documented_surface` + core `tests/test_plug_contract.py::test_example_plugin_passes_the_full_adapter_contract` |
| `test_two_image_plugs_are_listed_side_by_side` | 废弃：core 不再内置任何 plug；多插件并列由桥仓 `test_catalog.py` 的单插件安装/去重用例覆盖，不再有「双镜像插件」场景 |

### 2. fixture 版本号依赖

`tests/fixtures/legacy_lx06/plugin.yaml` 仍是旧内置版 `0.3.0` / `rui_compat >=0.3.0`；
core 测试只断言 manifest 结构/行为，不断言该版本字符串。删除夹具前需把
`tests/test_plugins.py` 等改用 `tests/fixtures/sample-plug`（本卡未完成，见 §4）。

### 3. `/health.adapter_plugins.registered` 口径（实测结论）

`registered` **不是**「目录里发现的插件」，而是 `device/loader.py:113` 的
`load_adapter_plugins()`：只统计显式 `RUI_ADAPTER_PLUGINS`、entry point、以及
`<data_dir>/plugs/*` 安装的 manifest。`RUI_PLUGINS_DIR` 里的插件由 registry 的
catalog 懒加载（日志 `image plug 'xiaoai-lx06' registered adapter 'lx06'`），不会进
`registered`。实测（`rui:0.7.6-b2`，挂 `rui-xiaoai-bridge/src` 到 `/opt/rui-plugins`）：

| 场景 | registered | audio_plane | planes |
|---|---|---|---|
| 只挂目录，无 env | `[]` | true | `living: running` |
| 加 `RUI_ADAPTER_PLUGINS=lx06=...` | `["lx06"]` | true | `living: running` |

**0.7.8 验收口径**：外置插件场景不要以 `adapter_plugins.registered` 为唯一判据；
用 `planes[].state=="running"` 或 `devices>=1` + `device_errors==0`。若验收脚本坚持看
`registered`，启动时必须**同时**带 `RUI_ADAPTER_PLUGINS` 与 `PYTHONPATH=/opt/rui-plugins`（compose 已两项都设）。只设前者会 `ModuleNotFoundError: rui_xiaoai_bridge`，`registered` 仍为 `[]` 并多一条 warning——0.7.8 验收脚本必须两项都带，否则会误判。

### 4. 夹具保留（Lead 已批准的 12c 拆分）

B1b（task-13）**不删除** `tests/fixtures/legacy_lx06`：core 仍有 17 个共享测试
`import xiaoai_plug`（`test_credit`/`test_kws`/`test_led`/`test_playback`/
`test_security_fixes`/`test_setup`/`test_setup_api`/`test_turn_audio`/`test_vendor`/
`test_device_services`/`test_adapter_plugins`/`test_xiaoai_reconnect_reset` 等）。这些
用例多数是设备语义（consumption pacing / LED ring / wire protocol / token 鉴权 /
vendor 事件 / 0.7.4 重连复位回归），不能靠换 mock 等价覆盖。
Lead 已批准拆出 **12c（task-14）**：逐文件分诊——能改用 `sample-plug`/`mock` 的留在
core，设备语义移植到桥仓，最后删除夹具。B1b 的工作树因此**全绿且无半成品**；夹具是
12c 的输入，不是遗留残渣。

### 5. 核心门禁

`git grep -in "xiaoai\|lx06\|小爱\|4412" -- src ':!src/rui/config/legacy_adapters.py'`
→ **0 命中**。`src/rui/web` 的 15 处已中性化（唤醒词回退删除、端口默认移入
`legacy_adapters.py`、文案改「设备插件」）；`web/src` 无命中。

### 6. 已知限制：未声明 `wake_words_residual` 的第三方清单

0.7.6 起，vendor 短语兜底由设备清单的 `wake_words_residual` 声明。若第三方清单
**未声明**该字段（或声明为空），core 不再 early-yield vendor 广播/回声，只靠后续
`is_echo` / `is_noise`；这类文本**可能进入回合**。这是已知行为变更，不是可用性保证。
建议所有设备清单显式声明 `wake_words_residual`；内置 lx06 清单已声明
`["小爱同学","小爱","小爱小爱"]`，保持旧行为。未补「不会进回合」的端到端测试——
做不到，就不写成保证。
