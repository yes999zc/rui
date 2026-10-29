# 音乐别名与短应答（0.8.7）

本文记录 0.8.7 A 卡的两处行为变化和一条运维要求：音乐别名进入意图主路径、别名文件权限
0640、以及 barge-in 后短应答的放行规则。

---

## 1. 别名进入音乐意图主路径

`data/music_aliases.json` 保存「用户怎么说 → 曲库里实际叫什么」的别名，例如：

```json
{"薯条匠": "薯条酱", "战车乐队": "Rammstein"}
```

0.8.7 起 `AliasStore.apply()` 不再是死代码：

- **位置**：在 `q.plan()` 之前对用户原文做替换，只走音乐路径；
- **入口唯一**：语音 `local.music_play` / `local.music_search` 与控制台
  `GET /api/v1/music/search?q=...` 共用 `MusicService.rewrite(text)`；
- **最长 key 优先**：嵌套别名（如 `薯条匠` 与 `薯条匠的歌`）不会被短 key 先替换坏；
- **不做全局替换**：天气、设备、聊天等其他路径完全看不到别名表。

实测例子：`放点薯条匠的歌` → 计划前改写为 `放点薯条酱的歌` → 曲库实际查询 `薯条酱`。

别名由 `NameResolver` 在「按原名搜不到 → 模型给出正式名 → 曲库命中」后自动记住，
也可以手工编辑上面的 JSON（改完重启生效；运行中的进程只在启动时读一次）。

## 2. 别名文件权限：0640

文件位置：`<system.data_dir>/music_aliases.json`（Docker 里通常是挂载卷下的
`data/music_aliases.json`；容器内默认是 `/var/lib/rui/music_aliases.json`，
对应 compose 里的 `./data:/var/lib/rui`）。

要求：**0640**（owner 读写，group 只读）。原因：fnOS 上旧文件曾被 ACL 弄成 000，
运维备份/查看时需要组读；但它不是凭据，不需要 0600 的强隔离。

- 0.8.7 起 `remember()` 写入时直接 `chmod 640`（临时文件替换后再次 chmod）；
- 启动/加载已有文件时 best-effort `chmod 640`，失败只打一条 `info` 日志，不影响音乐层；
  权限修复在读取之前执行，000 文件不会先被读成空表再覆盖；
- 手工修复：

```bash
# 宿主机（compose 挂载 ./data:/var/lib/rui，所以宿主文件在 ./data/ 下）
chmod 640 ./data/music_aliases.json
ls -l ./data/music_aliases.json      # -rw-r----- ...

# Docker（容器内 system.data_dir 默认 /var/lib/rui）
docker compose exec rui chmod 640 /var/lib/rui/music_aliases.json
docker compose exec rui ls -l /var/lib/rui/music_aliases.json
```

升级后第一次启动可以检查日志：

```bash
docker compose logs rui | grep -i "music alias"
```

文件丢失无妨：别名只是「学过的昵称」缓存，丢了会重新问一次模型并再次记住。

## 3. barge-in 后的短应答不再被当“没点歌”拒绝

旧行为：打断后用户说「行」「嗯」「好」，模型带着上一轮上下文调用 `music_play`，
反幻觉守卫看到句子里没有「放/播/歌」就拒绝，并让模型问「要听哪首？」。

0.8.7 起，只有当**存在媒体上下文**（音乐 `playing` 或 `paused`）且 transcript 归一化后
**≤4 字、命中封闭集合**时，才把它当作对上一轮的应答：

| 用户说 | 播放中 | 暂停中 |
| --- | --- | --- |
| 行 / 嗯 / 好 / 继续 / 别停 … | 保持播放（不重新搜歌） | 恢复播放 |
| 换一首 / 下一首 / 切歌 … | 下一首 | 下一首 |
| 停止 / 别放了 / 关掉 … | 停止 | 停止 |

规则是纯函数 `session.rules.music_answer_action(transcript, media_state)`，`TurnRunner`
通过注入的只读 `media_context`（app 传当前 `MusicService.state`）读取状态，不依赖全局
app。守卫把模型这次 `music_play` 改指向对应的 `music_resume` / `music_next` /
`music_stop` 工具；「保持播放」不需要工具，只回一句确认。

边界（防回归）：

- 没有媒体上下文（音乐 idle/未启用）时，「行」**不会**触发任何音乐动作；
- 不在封闭集合里的句子（如「今天天气」「好的时光」）仍按旧规则拒绝/交给模型；
- 非音乐路径、播放链路、barge/AEC/音量/followup 语义均未改动。

对应测试：`tests/test_music_alias_answers.py`（别名主路径、控制台 names、
`music_answer_action`、TurnRunner 守卫的 resume/hold/next/stop/拒绝分支、权限）。
