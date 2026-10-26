# 0.7.8 验收清单（Done 第 6 条 a–e）

> 用途：9 哥 亲手部署 0.7.8 时的**可判定**验收表。每条给命令、期望值、记录位。
> 白天执行；**d 会真的出声**，其余只读。升级/回滚步骤见 `docs/deploy-runbook-0.7.8.md`。
> 前置：已在 NAS 168 完成升级（或准备升级），`BASE=http://127.0.0.1:8080`，`TOKEN` 从环境变量取。

## a) 容器 healthy

```bash
docker ps --filter name=rui --format '{{.Names}}\t{{.Status}}'   # 期望 Up (healthy)
docker inspect --format '{{.State.Health.Status}}' rui            # 期望 healthy
```
判据：`healthy` 且重启次数 0。记录：____

## 前置：外置 lx06 插件必须已就位（0.7.8 删了内置实现）

升级前确认 `/vol1/rui/data/plugs/xiaoai-lx06/`（`plugin.yaml` + `devices/lx06.yaml`）已安装、`rui-xiaoai-bridge/src` 已只读挂载、`RUI_ADAPTER_PLUGINS` 含 `lx06=rui_xiaoai_bridge.adapter:build`、`PYTHONPATH` 含该路径——细节见 `docs/deploy-runbook-0.7.8.md` §4。**不做这一步，b 会从 2 台掉到 1 台。**

## b) 两台设备都被识别

```bash
curl -s $BASE/health | jq '{devices, device_errors, adapter_plugins}'
```
判据：`devices=2`、`device_errors=0`。
**`registered` 的正确读法（0.7.6 起）**：`adapter_plugins.registered` 只统计 `RUI_ADAPTER_PLUGINS` / entry point / `<data_dir>/plugs/*`；镜像里挂 `/opt/rui-plugins` 的插件走 catalog 懒加载，**不出现**在 `registered`。所以：
- 若 compose 里设了 `RUI_ADAPTER_PLUGINS=lx06=...` 且 `PYTHONPATH=/opt/rui-plugins` → 期望 `registered=["lx06"]`（dot2 若同样声明则在列）；
- 若没设 → `registered` 可能为 `[]`，**此时以 `planes`/`devices` 为准**，不要判失败。
记录：`devices=__ device_errors=__ registered=__`

## c) 两台都有音频平面

```bash
curl -s $BASE/health | jq '.planes'
curl -s $BASE/health | jq '.audio_plane, .multi_device'
```
判据：`planes` 数组里**两台都是 `state="running"`**（第二台不能是 `not_started`/`multi_device_disabled`）。
若第二台是 `multi_device_disabled`：config 缺 `multi_device: {enabled: true, max_planes: 2}`——补上重启再测；补完仍失败才算不通过。
记录：`planes=__ audio_plane=__ multi_device=__`

## d) 真机走一遍（会出声）

顺序与判据（每步都记时间戳）：

1. **唤醒**：对 living 说「你好小瑞」→ 期望 2 s 内有应答（cue/回复），`/health.last_turn` 或日志出现新回合；
2. **指令**：说「现在几点」→ 期望 5 s 内有语音回答，内容合理；
3. **播放**：说「放首歌」→ 期望开始播放；说「停」→ 期望 2 s 内停止（stop-push 判据 ≤300 ms，声学端到端 ≤600 ms 为另一指标）；
4. **第二台**：对 bedroom 说同一唤醒词 → 期望 bedroom 应答、living 不抢话；
5. **打断**（可选）：播放中说「停」→ 期望立即停。

判据：1/2/3/4 全过；失败项记「哪台、哪步、现象、日志行」。
记录：`唤醒__ 指令__ 播放__ 第二台__ 打断__`

## e) 升级 + 回滚各一次

```bash
# 升级：按 runbook 第 2–5 步，记录起止时间与 /health 五字段
# 回滚：按 runbook 第 6 步，回到 rui-dot2:1.3.1，记录 /health 恢复
```
判据：升级后 a–c 全绿；回滚后 `devices=2`、`device_errors=0`、`console=legacy`（0.7.2 的行为）恢复。
记录：`升级耗时__ 回滚耗时__ 回滚后字段__`

## 汇总表（贴回 HANDOFF）

| 项 | 判据 | 结果 | 证据（命令输出/日志行） |
|---|---|---|---|
| a healthy | Up (healthy) | | |
| b devices | 2 / 0 | | |
| c planes | 两台 running | | |
| d 真机 | 1–4 全过 | | |
| e 升级/回滚 | 各一次且可恢复 | | |
