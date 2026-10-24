# 剩余工作清单（2026-10-23 品牌轮之后）

> 本文件是细节台账；**计划、优先级与判据以 `docs/ROADMAP.md` §3（P1–P3）为准**；
> 历史过程见 `docs/archive/HANDOFF-history-to-2026-10-24.md`。

来源：`docs/ROADMAP.md`、`docs/HANDOFF.md`、内部审计（未公开）、内部审计（未公开）、
内部审计（未公开）。按「挡住 v1 验收 → 真机遗留 → 安全/运维 → 体验」排序。

## A. v1 验收门槛（需要 9哥 / 外部条件）

1. **仓库公开 + GHCR public ✅**（`ghcr.io/yes999zc/rui:latest` 匿名 pull 200，amd64/arm64）；
   一键装 raw 200；`deploy/install.sh` 仍保留「拉不到镜像就从公开源码 build」回退。
2. **git 历史凭据 ✅**：旧公开历史已通过删库重建 + filter-repo 清除；旧 commit/raw 已不可访问；
   device/console token 已换新；音箱 root 密码仍受 squashfs 只读限制（见 C12）。
3. **NAS 部署 ✅ 2026-10-23**：本机 Docker 构建 + NAS 运行 + 音箱切换 + LAN/Tailscale 实测
   （health、KWS/VAD、debug say、speaker audition）；一键脚本可用。
4. **D3 唤醒门槛 ✅ 2026-10-24**：80% 音量放歌 **19/20**（2–3 m）；3–4 米远场正式计数仍未做（只记录项）。
5. **打断 ≤300 ms 口径 ✅ 2026-10-24**：服务端停推 24.2 / 64.5 / 205.2 ms（max ≤300）；
   设备缓冲听感「干脆」；首次 447.3 ms 为回答自然收尾伪影。

## B0. 交互缺陷（已定位，待修）

- **控制台首次登录 token 不可见（issue #4，2026-10-24）**：全新 0.2.0 冷装后用户不知道
  `web.auth_token`，install.sh 只在安装输出里打印一次。期望：首次打开未认证控制台 → 创建控制台
  密码/token 页（写入 config），或安装结束醒目展示 + 固定文件；控制台提供轮换入口。优先级 P2。

- **回复结束后的免唤醒追问首字丢失**：已按旧项目方案实现 follow-up echo-tail replay
  （`7693a4f`）：`pacer.due` 到 gate arm 之间缓存麦克风帧，arm 后回放给 VAD；日志会打印
  `follow-up gate replayed … ms of gated tail`。**已部署 NAS，9哥 真机复测「紧着说明天呢」成功 ✅。**

## B. 真机 / 功能遗留

6. **音箱客户端安装路径 ✅（2026-10-24）**：`GET /api/v1/devices/{id}/connect` + Setup「音箱连接」卡
   （ws URL/token/复制/未配置提示）+ `docs/open-xiaoai-client.md`；**冷装已真机演练通过**（423 s 到第一句）。
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
13. 安全审计 P2：安全响应头、鉴权失败日志、用户数据文件 0600 已完成；剩余 SSRF/端口探测
    allowlist、容器非 root、依赖 CVE 扫描（不阻塞 v1，排后续）。
    **新增（2026-10-24 验收发现）**：config 被清空成 0 字节时会静默使用 schema defaults → `insecure_bind`
    拒绝 → crash loop，未被 last-good 接管；应让空文件走 ConfigError 触发恢复（小修，待做）。
14. P15b 镜像重建（品牌/新功能之后）与 GHCR 发布复验：品牌轮镜像已重建并 public ✅。

## D. 体验 / 控制台

15. 对话页手机卡片化（表格在 390px 仍挤）；overview 信息重复/空块可再收。
16. ProvidersView 阶段标题仍 22px（其余控制台已 17px）；手机个别 provider 控件 <44px；
    桌面 provider 输入框 32px，可选提到 40px。
17. 中文未内嵌 CJK webfont（保持系统栈）；如要求全平台一致需再定。
18. Logo GIF 未导出（页面用 SVG/CSS 动效）；需要文档/社媒素材时可抓帧生成。
19. ✅ 改名完成（0.2.0）：repo/镜像/Python 包/CLI/配置路径/环境变量统一为 `rui`；旧变量名按迁移说明回退。

## E. 本轮已完成（不要重复）

ASR 用量额度卡、Providers v2、SiliconFlow TTS 下线、通用 MCP（含 stdio 安全闸）、控制台
P0 polish、音乐凭据 UI、设备 plane_error、MCP test env、小瑞/Rui 品牌、本地字体、2026 Pantone
配色、动效像素 logo、音箱优先的音色试听。

## F. License（待 9哥 拍板）

- 当前 MIT；讨论见本轮会话。候选：MIT（最简单）、Apache-2.0（专利/商标条款、更企业友好）、AGPL-3.0（防闭源托管）。
- 容器内的 ffmpeg 已改为从固定版本源码构建的 LGPL-only 二进制（P1.6，见 `THIRD_PARTY.md`）；它仍是第三方组件，MIT 代码许可证不覆盖它。

## G. CPU / 镜像（2026-10-23 定位）

- 镜像 431 MB 主要来自 base python(119 MB) + 静态 ffmpeg(129 MB) + site-packages(178 MB)，
  `/app` 只有 1.9 MB；**模型在 `/data/models` 卷里，不在镜像内**。
- NAS CPU 高不是模型/镜像导致：KWS 用 2 个 ONNX 线程时 3 个 worker 各 100%（约 3 核）；
  改成 `kws_num_threads: 1` 后容器 CPU 从约 319% 降到约 31%。
- NAS 的 ASR chain 已改为 `volc-asr` 单链路；`local-asr` 仍保留在 catalog（插座），不默认加载，
  省约 350 MB 内存。

## H. 远程通道（M0–M7 已完成）

- Telegram + 微信 iLink/ClawBot 的 backend、gateway、Channels 页已完成；测试 743 passed。
- 真机验收仍需要：Telegram allowlist chat_id、微信扫码、两通道语音/文字测试。
- M7（托管 gateway 自动启动/watchdog、控制台 Start/Stop/Restart、NAS 部署）已完成；
  NAS 当前只启用 Telegram，微信只保留接口。NAS Telegram 出网走本机 mihomo SOCKS；
  mihomo IPv6 已关闭并选择可用节点，Node 选择为运行时状态（重启后若失效需在面板重选）。
- **v1 冻结（2026-10-24）**：四道门槛当日全部通过（冷装 423 s、唤醒 19/20、barge max 205 ms、
  公开仓 ✅）；Telegram 4/4、微信接口保留、配置恢复/备份恢复 smoke 通过。最新代码
  **748 passed / 17 skipped**（无模型环境；模型环境 764/1）；NAS 尚未部署新镜像（等 9 哥 明确指令）。

## I. 多类型音箱适配标准（2026-10-24 记录，未排期，冻结期只记录不动代码）

- 想法（9 哥）：接入机型变多后，需要一份**标准文件**说明「不同权限等级的音箱怎么适配」，
  免得每台机器都重新摸一遍；建议 `docs/speaker-adapter-standard.md`，v1 四门槛过后再开写。
- 先按**权限等级**分两类（做能力矩阵，不按品牌逐个写死）：
  - **A 类 · 与原厂共存 / 低权限**（小爱 LX06 这类）：无 root、固件只读、不能改原厂；
    client 只能跑在设备上，走厂商已开放的 SSH/ubus/串口等通道；必须处理与原厂语音的让位/接管
    （vendor takeover）、原厂音量/灯效边界，能力上限取决于厂商暴露的接口。
  - **B 类 · 高权限 / 完整控制**（dot2 这类）：可 root/shell、装系统服务、自启动；可直接控制
    ALSA/播放、灯效、按键、音量，能拿到更多音频通道与设备状态；但仍需保留原厂恢复路径。
- 标准文件至少覆盖：能力矩阵（麦克风通道/AEC/播放/灯/音量/按键/自启动/root/原厂共存）、
  client 接入契约（ws URL + token + 端口 + 心跳/重连）、无能力时的降级（无灯、无播放、无 AEC）、
  移植步骤与验收清单、已知机型差异与故障排查。
- 原则：机型差异全部收在 adapter 层，控制面/音频面不为某个品牌加分支；现有 LX06 实现作为 A 类样板。

## J. 后台设备管理参考 EchoMuse（2026-10-24 记录，未排期，冻结期只记录不动代码）

- 想法（9 哥）：rui 控制台「设备管理」的**后台逻辑可以参考 EchoMuse 的 Device Management，能照抄就照抄**
  （上游 `wilbowes/EchoMuse` controller，MIT；本机部署在 `01_Code/services/echomuse-controller`，
  镜像 `ghcr.io/wilbowes/echomuse-controller`，dashboard 与 HA add-on 同一套 controller）。
- 值得对照/照抄的点：设备注册与 approve 流程、setup wizard（USB 发现 → 装固件 → 连 Wi-Fi → 审批）、
  每设备配置（唤醒词在设备端还是 controller、自定义唤醒词、OTA）、在线状态与重连、
  多设备「谁听得最响谁答」、每设备端口/证书分配、数据库模型（devices/config/users）、控制台页面结构。
- 边界：只借鉴后台逻辑与交互，**不引入 HA Assist 依赖**（EchoMuse pipeline 硬编码 HA Assist，rui 自己就是助手）；
  rui 仍由 adapter 层吸收机型差异（见 `rui-dot2-bridge/docs/adr-0001-adapter-as-controller.md`）。
- 时机与合规：v1 四门槛过后再评估；若照抄代码需保留 MIT 版权/许可声明并注明来源。

## K. 长期架构：RUI 是插座，音箱方做插头（2026-10-24 记录，未排期，冻结期只记录不动代码）

- 9 哥原话/理想状态：音箱单独维护、RUI 也单独维护；RUI 只定义好**规则（插座）**；音箱维护者
  （包括我们自己）按规则把该机型的各类参数封装成**插头**；插头插进 RUI 后台的插座里，直接就能连上。
- 我的理解（可纠偏）：**插座** = RUI 侧稳定契约（`DeviceAdapter` + 能力声明 + 传输/连接生命周期 +
  配置 schema + 控制台呈现），核心代码不出现品牌分支；**插头** = 一份机型描述包（机型/权限等级 A 或 B、
  能力矩阵、client 安装与连接方式、协议版本、参数 schema，加可选的 adapter 实现）；
  **插入** = 控制台添加设备时选插头/填参数 → RUI 校验 → 生成连接信息并启用，不用改 RUI 代码。
- 已经有的地基：`src/rui/device/contract.py` 的 `DeviceAdapter`/`Capability`/`Transport` 就是插座雏形；
  `registry.register(name, target)` 是插头注册钩子；`rui-dot2-bridge` 已用「adapter 放本仓库、不改 rui 核心」
  的方式真机跑通，是第一个真实插头样板。
- 还缺（将来做）：插头的**声明格式**（manifest：机型/能力/参数 schema/依赖/兼容版本）、安装与校验流程、
  控制台「从插头添加设备」的 UX、第三方插头信任模型（签名/allowlist/权限声明）与版本兼容矩阵。
- 开放问题：插头只做**纯声明**，还是允许带**可执行 adapter 代码**？（倾向：先纯声明 + 内置 adapter；
  第三方自定义协议再走受限 adapter 包 + 签名。）
- 关系：§I 是适配规范、§J 是可抄的设备管理后台、§K 是把两者合起来的最终形态；第一个候选插头是
  Dot2（B 类，正在另一个对话里开发），现有 LX06（A 类）作为样板。
