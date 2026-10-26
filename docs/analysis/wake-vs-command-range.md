# 唤醒 vs 指令拾音不对称（task-16）

- 现象（9 哥 真机）：Dot2 3–4 m 能唤醒，但唤醒后说指令要走近到 ~0.5 m。
- 采样镜像：**`rui-dot2:1.3.0-p21`**（0.7.1 + P2-1，2026-10-26 16:08 部署）。task-18 的 `p22` 于 17:15 才部署，**没有参与本文件的数据**。
- 本文件只做数据与归因，**未改任何生产参数**；缺失处标「证据不足」，每条数字可回溯到文件与行号。

## 0. 方法与其局限（先读）

**不能移动音源**（无人现场、纪律不允许搬设备），所以用**等距 + 分级增益**作为距离的代理指标：同一位置、同一份 48 kHz/16-bit WAV，只改文件幅度。

| 档位 | 含义（相对满幅） |
|---|---|
| `1.00` | 满幅 |
| `0.75` | −2.5 dB |
| `0.50` | −6 dB |
| `0.30` | −10.5 dB |
| `0.15` | −16.5 dB |

音源与设备：
- Dot2（卧室，`device_id=dot2`）：Mac Studio `afplay`，系统音量 50；marks `/tmp/fleet6/marks-level-dot2.csv`
- LX06（客厅，`device_id=living`）：荣耀服务器 `aplay -plughw:2,0`（ALC256，Master 87 = 100%）；marks `/tmp/fleet6/marks-level-lx06.csv`
- 日志：`/tmp/rui-fleet9.log`（2000 行窗口、3 s 轮询）
- 复算：`.venv/bin/python scripts/fleet_matrix.py --log /tmp/rui-fleet9.log --marks <marks> --log-tz UTC --window 12`，按 `round`（`<gain>#<轮>`）分组

协议：每句「你好小瑞 → 停 1.5 s → 指令」，句间 10 s，每档 2 轮 × 4 句（hello/time/stop/bye，**去掉 `放首歌/下一首`**）。

**已知污染（本文件单列拒绝原因，**不计为拾音失败**；下表分母是全部 8 次尝试——含被 `turn_open` 挡掉的那几次，所以 Dot2 `1.00` 是 adm 3/8 而不是 3/6）**：
1. 句间 10 s 偏短 → 上一轮 turn 未关，下一句被 `turn_open` 拒：Dot2 共 4 次（1.00×2、0.30×1、0.15×1），LX06 共 **8 次**（1.00×1、0.75×1、0.50×2、0.30×2、0.15×2）。
2. `lost_playing` 残留：LX06 在 1.00 档出现 1 次（放歌队列长期占播放态）。
3. 满幅档 1.00 在 Mac 侧有削波风险；但**本次 fleet9 日志里最大 `peak` 为 0.5866，没有出现 1.0000**，所以本次数据不能用来证明「1.00 档削波」。
4. `turn` 列是**窗口内 turn 总数**，含同窗 follow-up（例：Dot2 `0.15` turn 5 > adm 4），**不是指令成功率**。

## 1. 电平响应（candidate / admitted / turn）

**Dot2**（每档 8 次尝试）：

| 档位 | wake candidate | admitted | rejected | turns（含 follow-up） |
|---|---|---|---|---|
| 1.00 | 5/8 | 3/8 | `turn_open`×2 | 3 |
| **0.75** | **8/8** | **8/8** | 0 | 6 |
| 0.50 | 7/8 | 7/8 | 0 | 6 |
| 0.30 | 6/8 | 5/8 | `turn_open`×1 | 5 |
| 0.15 | 5/8 | 4/8 | `turn_open`×1 | 5 |

**LX06 / living**（每档 8 次尝试）：

| 档位 | wake candidate | admitted | rejected | turns（含 follow-up） |
|---|---|---|---|---|
| 1.00 | 7/8 | 5/8 | `turn_open`×1、`lost_playing`×1 | 5 |
| 0.75 | 8/8 | 7/8 | `turn_open`×1 | 4 |
| 0.50 | 7/8 | 5/8 | `turn_open`×2 | 3 |
| 0.30 | 6/8 | 4/8 | `turn_open`×2 | 1 |
| 0.15 | 6/8 | 4/8 | `turn_open`×2 | 0 |

**能说的（证据强度：中）**：`0.75 → 0.15` 区间里两台设备的 candidate 都随增益下降而减少（Dot2 8→7→6→5；LX06 8→7→6→6），且**幅度接近**；`1.00` 档是异常点（Dot2 5/8 < 0.75 的 8/8），原因未查明（可能与播放器增益/削波有关，但本次日志峰值仅 0.5866，不足以证明）。差别出现在 admitted 之后：LX06 在低档 turn 掉到 0–1，Dot2 仍有 5。注意 turn 含 follow-up，这条只能作为「链路仍有反应」的弱证据，不能直接读成指令成功率。

## 2. 「更响反而更差」的现场手记（证据强度：低）

同一位置只改播放器增益（`afplay -v 1` vs `-v 2`），wake-only 各 5 次：**5/5 vs 3/5**。

- 这是**现场手记**，没有留下 marks 与对应日志窗口，无法复算；引用时请按「低强度」处理。
- 机制上削波伤 KWS 是已知现象（task-6 采样里确实见过 `[seg] … peak=1.0000`，例如 `/tmp/rui-fleet3.log:414`），但**那份日志是另一份 task-6 采样（15:01 run）的，不是本节的 -v2 实验**，不能混用。
- 结论仅到「更响不一定更好，别用调音量代替距离标定」，不主张具体阈值。

## 3. 两条链的参数对照（截至采样时的生产值）

| 环节 | 唤醒链（wake） | 指令链（command） | 出处 |
|---|---|---|---|
| 判定位置 | 服务端 RUI KWS（`rui.audio.kws`）；设备端 OWW **off** | 服务端 VAD + 本地 ASR | 生产 override `DOT2_OWW_ON_DEVICE=off`；`src/rui/audio/vad.py` |
| 上行增益 | 不经过 | `DOT2_MIC_GAIN_DB=18`（bridge 侧） | 生产 override（NAS `/vol1/rui/rui-dot2-override.yml`，本机不可达，无行号） |
| AEC | 不涉及 | `DOT2_AEC_ENABLED=1`；bridge 软件门控 `DOT2_PLAYBACK_GATE_MS=0` | 同上；`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py`（`_gate_mic`） |
| 回复后 follow-up 静音窗 | — | `post_playback_mute_ms=2500`（仅对**有 TTS 回复的 follow-up** 设 `_mic_mute_until`，`process.py:734-744`，caller `:2361/:2383`） | 生产 `config.yaml` |
| cue 回声忽略窗 | — | `ack_echo_ignore_ms=2500`（作用于 ack cue 回声判定，`process.py:719-726`、`:1848`；与上面的 follow-up 静音窗不是同一机制） | 生产 `config.yaml` |
| 端点/超时 | 唤醒后 5 s 无语音 → `wake without speech` | VAD hold/preroll、`[seg]` 判决 | `src/rui/audio/vad.py`（`GateConfig`） |
| 门槛 | KWS 关键词命中即出 candidate | 需 VAD 判 speech 才送 ASR | `src/rui/audio/process.py`（`admitted` 后等 `[seg]`） |

**差异最大的三个参数（方向判断，不主张已证）**：
1. `DOT2_MIC_GAIN_DB=18`：只有指令链经过它；唤醒链不经过，因此「能唤醒、听不清」在这个参数上是有解释力的。
2. `src/rui/audio/vad.py` 的 `GateConfig`（VAD 阈值/端点，两台共用）：远场小声容易 `speech=0`，而 KWS 用另一套特征。
3. `post_playback_mute_ms=2500`：只作用于**有 TTS 回复的 follow-up**，**放歌停止时不会设这段静音**（见 §5 H2 更正）。

## 4. Dot2 设备侧可调项（它的相对优势）

| 旋钮 | 当前值 / 读法 | 可调范围 | 改法 | 需重启 | 回滚 |
|---|---|---|---|---|---|
| `DOT2_MIC_GAIN_DB` | 18（生产 override） | 保守 0–24 dB，每次 6 dB | 改 override + `compose up -d` | 是（容器） | 改回 18 |
| `DOT2_AEC_ENABLED` | 1 | 0/1 | 同上 | 是 | 改回 1 |
| `DOT2_PLAYBACK_GATE_MS` | 0（bridge 软件静音已关） | 0–2000 ms | 同上 | 是 | 改回 0 |
| 设备端 OWW 阈值/模型 | `DOT2_OWW_ON_DEVICE=off` 时**休眠**；`DOT2_OWW_THRESHOLD=0.4`、模型 `hey_jarvis_v0.1`（英文）——**本次未核**其在生产 override 中的当前值 | 需中文模型（task-3 BLOCKED） | 环境变量（阈值）/ adb、`/shell`（模型） | 阈值否、模型是 | 清空变量 |
| 设备音量 | `DOT2_VOLUME_CAP=100`；实际播放音量由 `/loudness` 校准 | 0–100 | v2 控制台 / `/api/v1/device/volume` | 否 | 记录原值改回 |

**判断**：设备侧里只有 `MIC_GAIN_DB` 与 AEC 开关直接作用于指令链，**两者都已开启、未发现未开启项**；增益 / AEC / VAD 三者仍需 H1/H3 实验区分（见 §6，本文件不排序）。

## 5. 竞争假设与判别实验

| # | 假设 | 判别实验 | 目前证据 |
|---|---|---|---|
| H1 | 上行增益不足（远场电平低 → VAD 不判 speech） | `MIC_GAIN_DB` 18→24，看 `[seg] rms/peak` 与 `speech=` 是否变化 | **未做**。间接：0.15 档仍能 wake（5/8），说明 KWS 收得到，但指令链反应变少 |
| H2 | 播放结束后 2.5 s 静音窗吞掉指令 | 放歌 → 立刻说「停止播放」vs 等 3 s 再说，比对 `[seg]` | **不成立（机制被代码否证）**：`post_playback_mute_ms` 只在**有 TTS 回复的 follow-up** 时设 `_mic_mute_until`（`process.py:734-744`、caller `:2361/:2383`），**放歌停止不设**；task-6 里 `/tmp/rui-fleet3.log:601` 的段也被 VAD 采到（`speech=0.88 s`），说明麦克风没被整段静音 |
| H3 | AEC 参考延迟不匹配，把近端语音一起消掉 | `DOT2_AEC_ENABLED=0` 复测播放中指令 | **未做**。task-6 `/tmp/rui-fleet3.log:601` 的 `peak=0.0415` 近乎静音，与「被消掉」相容，但也可能是距离太远 |
| H4 | 波束/朝向 | 设备转 90° 复测 2 m | 未做（需现场） |

优先顺序：H1（一次 6 dB，零结构改动）→ H3（关 AEC 一轮）→ H4。

## 6. 结论与下一步（按证据强度写）

- **能确定的**：Dot2 的**唤醒链**与 LX06 同量级（0.15 档 5/8 vs 6/8 candidate）；差距出现在**唤醒之后**（admitted → ASR 这一段），与 9 哥 的主观「能唤醒、听不清指令」一致。
- **不能确定的**：是增益、AEC 还是 VAD 门槛导致的。H1/H3/H4 都**未做实验**，H2 被代码否证。**不排序、不排除**。
- 给 `[impl]` 的卡草案（另开，不在本卡；与 task-19 合并到同一次真机会话）：
  1. **远场 VAD 门槛自适应**：按最近 N 秒底噪估计动态阈值（预期：2 m 小声能起判）；改 `src/rui/audio/vad.py` 的 `GateConfig`。
  2. **上行增益一次性标定**：`MIC_GAIN_DB` 18→24 做 A/B（只改 override，可回滚），用 `[seg] rms/peak` 判是否过载。
  3. 验收（可判定）：**2 m 内指令一次成功率 ≥ 80%**（同句、同房间、5 次 × 2 档，日志 + transcript 可核）；**唤醒后 3 s 内说指令不丢开头**（transcript 与所说一致、无「在呢」污染）。

## 7. 校准 + 人声复测（2026-10-26 18:12–18:18）——**本节更正 §1–§6 的结论**

### 7.1 校准（按 9 哥 的「先校准再下结论」）
- 音源：Mac Studio 内置扬声器；`afplay`；**系统音量 100**；WAV 峰值由 −5.0 dBFS 归一到 **−1.0 dBFS**（`ffmpeg -af volume=4dB`，`max_volume: -1.0 dB`，不削波）。
- 校准依据用**设备侧日志**自检（无需声级计）：播 1 句后 `[seg] rms=0.0569 peak=0.6119`、`rms=0.0245 peak=0.6090` → 落在目标区间（rms 0.02–0.10、peak < 0.98）。
- **已知偏差（重要）**：§1–§6 的全部 TTS 采样用的是 −5 dBFS 峰值 + 未标定的播放链，实测语音段 rms 只有 **0.006–0.02**（比校准后低 6–20 dB）。9 哥 用耳朵也判「Mac 测试音太小」。**因此 §1–§6 关于「指令要凑近」的结论不再作为产品结论引用。**

### 7.2 真人语音批次（与 TTS 批次分开标注，不混入同一矩阵）
协议：每句「你好小瑞 → 停 1.5 s → 指令」，句间 10 s；2 个距离 × 3 句 × 3 次 = 18 句。
音源：**真人**（9 哥），Dot2，卧室；日志 `/tmp/rui-human2.log`。

| 距离 | kws 命中 | admitted | rejected | 「几点了」正确 | 「放首歌」起播 | 「下一首」换歌 | 语音段 rms / peak |
|---|---|---|---|---|---|---|---|
| **1 m** | 12（≥9 句，无漏） | **12** | **0** | **3/3** | **3/3**（`playback requested`） | **3/3**（`playback requested`） | 0.030–0.073 / 0.26–0.74 |
| **2 m** | **9** | **9** | **0** | **3/3** | **3/3** | **3/3** | 0.019–0.048 / 0.14–0.33 |

- 「下一首」没有 turn 文本是因为 `follow-up gate stays shut: music owns the speaker`——**音乐占用扬声器时按设计不起新对话**，换歌仍然发生；不能读成失败。
- 两次 `wake without speech` 出现在 1 m/2 m 的个别轮次（用户停顿超过 5 s），属协议节奏，不是漏唤醒。

### 7.3 结论更正（按 9 哥 的规则：伪影必须显式更正）
- **「唤醒灵敏、指令要凑近」在 1–2 m 的真人语音下不复现**：1 m 与 2 m 都是唤醒 100%、三类指令各 3/3。
- 原结论的主要成因是**激励源电平过低**（未校准，rms 0.006–0.02），属**装置伪影**；§1–§6 的「差距在 admitted→ASR」只在低电平激励下成立。
- 仍需查的：**音乐播放中**（真实音乐 + 人的短指令）在 2 m 是否同样稳——本批次里「下一首」是换歌成功但无文本，需要一次「音乐中 + 短指令 + 有/无文本」的专项（task-19 的验收），而不是靠距离梯度。

### 7.4 对 task-19 的直接影响
- p23 的 A/B 必须用**校准后的人声**重做（TTS 激励下 3/10 的结果作废）。
- 判定标准不变：音乐中「下一首」「停止播放」各 5 次，ASR 正确 ≥4/5（「下一首」允许以 `playback requested` 作为功能成功，文本仅作辅助证据）。
