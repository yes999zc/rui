# 剩余工作清单（2026-10-23 品牌轮之后）

来源：`docs/ROADMAP.md`、`docs/HANDOFF.md`、内部审计（未公开）、内部审计（未公开）、
内部审计（未公开）。按「挡住 v1 验收 → 真机遗留 → 安全/运维 → 体验」排序。

## A. v1 验收门槛（需要 9哥 / 外部条件）

1. **仓库已公开 + raw 安装脚本 200 ✅**；**GHCR 包仍 private**（GitHub REST 不支持个人 package
   改可见性，需网页 Settings 一次点击）→ 一键装 pull 仍 401；`deploy/install.sh` 已加「拉不到镜像就从公开源码 build」回退，所以一键装本身可用。
2. **git 历史凭据**：round-1 记录的旧 commit 含真实凭据/LAN IP/用户名；公开前必须全历史扫描 →
   轮换所有真实凭据 → filter-repo/BFG 重写（需 9哥 授权，会冻结并行工作）。
3. **NAS 部署 ✅ 2026-10-23**：本机 Docker 构建 + NAS 运行 + 音箱切换 + LAN/Tailscale 实测
   （health、KWS/VAD、debug say、speaker audition）；「一键脚本」还差 GHCR public。
4. **D3 唤醒门槛**：80% 音量放歌 20 次唤醒 ≥19；含 3–4 米远场正式计数。
5. **打断 ≤300 ms 口径**：当前服务端 `silence_after_ms=0`，但设备端剩余 lead 约 420 ms，需按
   300 ms 口径实测并决定是否继续调。

## B0. 交互缺陷（已定位，待修）

- **回复结束后的免唤醒追问首字丢失**：follow-up gate 在 `pacer.due` 后 1.0 s 才 arm，这 1 秒的麦克风
  帧没有 buffer/replay；「紧着说『明天呢』」会丢首字，等 1 秒后说才正常。旧项目用 `ECHO_TAIL=0.7`
  + `echo_tail_buf` replay 解决（`assistant_v3.py:2771,3122,3242`）。计划：缓存 due→arm 的帧，arm 后
  回放给 VAD；加单测 + 真机验证。**9哥 要求先不改，已加入本轮 TODO（task-27）。**

## B. 真机 / 功能遗留

6. **音箱客户端安装路径**：新用户没有 open-xiaoai client 安装步骤、没有「显示设备 token + 复制
   ws URL」的界面；冷装会卡在「没有声音」（功能审计 P1-8）。
7. **P12 / E1 第二台设备**：每设备音频配置（config v3）、无播放能力降级；等第二台设备到货。
8. **B2 剩余**：远场唤醒计数、音量对账三个调用点、音乐收藏/口味 UI、farewell 静音续歌边角、
   gate-over-music 清理；ACK earcon 与老项目差异仍未解释。
9. **V2 三原语**：HA 场景的 clarify（缺动词问回）/ choose（多命中）/ confirm（危险确认）仍需
   按 `docs/interaction.md` 逐条验收。
10. **真实 NAS 音乐凭据**：本轮只做了 UI 与接口；需在 NAS 上真实保存 Navidrome 凭据并点测试连接。

## C. 安全 / 运维

11. **dev 配置匿名**：当前 :8091 为 `host=0.0.0.0` + `allow_anonymous=true` + 设备
    `allow_no_token=true`，只适合本机开发；**不要复制到 NAS/生产**。
12. 设备 token/控制台 token 已轮换；**音箱 root 密码因 squashfs 只读未能改**（建议厂商方式/仅可信 LAN）。
13. 安全审计 P2：安全响应头、鉴权审计日志、SSRF/端口探测 allowlist、用户数据文件 0644 →
    0600（memory/music_history/reminders/.ha_entities）、容器非 root、依赖 CVE 扫描。
14. P15b 镜像重建（品牌/新功能之后）与 GHCR 发布复验。

## D. 体验 / 控制台

15. 对话页手机卡片化（表格在 390px 仍挤）；overview 信息重复/空块可再收。
16. ProvidersView 阶段标题仍 22px（其余控制台已 17px）；手机个别 provider 控件 <44px；
    桌面 provider 输入框 32px，可选提到 40px。
17. 中文未内嵌 CJK webfont（保持系统栈）；如要求全平台一致需再定。
18. Logo GIF 未导出（页面用 SVG/CSS 动效）；需要文档/社媒素材时可抓帧生成。
19. 完整改名：repo/package/CLI/README/GitHub 仍是 house-ear；本轮只改显示层。

## E. 本轮已完成（不要重复）

ASR 用量额度卡、Providers v2、SiliconFlow TTS 下线、通用 MCP（含 stdio 安全闸）、控制台
P0 polish、音乐凭据 UI、设备 plane_error、MCP test env、小瑞/Rui 品牌、本地字体、2026 Pantone
配色、动效像素 logo、音箱优先的音色试听。

