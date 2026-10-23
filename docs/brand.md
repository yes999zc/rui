# 品牌与视觉：小瑞 / Rui（2026-10-23）

## 1. 名字

- **产品显示名：小瑞（Rui）**，与唤醒词「你好小瑞」一致；英文环境显示 `Rui`，旁边保留小号「小瑞」。
- 本轮只改用户可见品牌：浏览器 title/meta、侧栏、移动端 header、i18n 品牌文案、favicon。
- **内部标识暂不改**：repo、Python 包、CLI（`house-ear`）、路由、localStorage key、面板 ID 全部保持
  `house-ear`，避免升级时丢配置/书签/会话。完整改名（repo/CLI/README/GitHub）需要单独迁移窗口。

## 2. 字体（本地加载，无第三方请求）

| 用途 | 字体 | 文件 |
|---|---|---|
| 展示标题 | Bricolage Grotesque（variable 400–800） | `web/public/fonts/bricolage-grotesque-latin*.woff2` |
| 正文/UI | Manrope（variable 400–800） | `web/public/fonts/manrope-latin*.woff2` |
| 数字/日志/代码 | JetBrains Mono（variable 400–700） | `web/public/fonts/jetbrains-mono-latin*.woff2` |

- 全部 `@font-face` 指向 `/fonts/*.woff2`，`font-display: swap`，只含 latin + latin-ext；
- 中文回退系统栈：PingFang SC / HarmonyOS Sans SC / Noto Sans CJK SC / Microsoft YaHei；
- 许可证：SIL OFL 1.1，见 `web/public/fonts/OFL.txt` 与 README；
- 构建后实测：`fonts.googleapis.com` / `fonts.gstatic.com` **0 请求**。

## 3. 配色（2026 Pantone 方向，克制版）

调研依据：Pantone 2026 Color of the Year **Cloud Dancer 11-4201**，以及 NYFW SS26 runway
色盘（Acacia / Marina / Muskmelon / Alexandrite / Lava Falls 等）。

| Token | Light | Dark | 用途 |
|---|---|---|---|
| bg | Cloud Dancer `#f1efe9` | 深绿黑 `#121715` | 页面底色 |
| surface | `#fcfaf5` | 深一档 | 卡片/面板 |
| ink | Coffee Bean `#2a211c` | 浅暖灰 | 正文 |
| accent/primary | Alexandrite 深青 `#0e6b63` | `#63c7b8` | 主按钮/焦点 |
| live | Muskmelon `#d98a3e` | `#f0b36b` | 唤醒/在线/音乐 |
| ok / warn / fail | Sage / Caramel / Lava Falls | 重新取值 | 语义状态 |

对比度实测：muted 4.97/5.51、accent 5.53/8.97、warn 5.70、fail 5.79、ok 5.33（light/dark
均过 AA）。设计原则：专业后台优先，年轻感由字体 + 2–3 个语义强调色 + 动效承担，不做彩虹。

## 4. Logo

- `web/src/components/BrandMark.vue`：inline SVG 像素 mark，主题感知，侧栏 30px / 移动 24px；
- 元素：三段声音柱（声音/耳朵）+ 数据 rail + 左右节点 + 流动数据包（数据传输连接）；
- CSS `steps()` 动效约 2.2s；`prefers-reduced-motion` 下动画关闭；
- `web/public/favicon.svg` 为静态版；页面不依赖 GIF/重资产。需要文档/社媒 GIF 时可从 SVG 抓帧导出。

## 5. 音色试听出口（音箱优先）

`POST /api/v1/providers/{stage}/{name}/audition`：

- `output=speaker`（默认 UI 选项）→ 合成 PCM，写 `<data_dir>/cache/auditions/*.wav`（0600，30 min/20 个上限），
  经音频面 `PlayFile` 排队播放，返回 `{"played":"speaker","duration_ms":N,...}`；
  语义是「已交给音频面，会排在当前回答/歌曲之后」，不是播完。
- `output=browser`（缺省/显式）→ 旧的 `audio/wav` 响应，浏览器播放。
- 音箱不可用（plane 未运行/未连接/下发失败）→ 503，前端自动回退 browser 并提示。
- 控制台 toggle「在音箱试听 / 在本机试听」，默认音箱，localStorage 记忆。
