# 冷装到第一句话：安装流程审计（RUI 0.2.1，HEAD 0a5446a）

> 只读审计，未改代码、未提交。视角是“第一次装、没有 NAS 经验、只有 LX06 和必要密钥”的用户。
> 依据：`deploy/install.sh`、`README.md`、`docs/deploy.md`、`docs/install-wizard-design.md`、
> `docs/open-xiaoai-client.md`、`docs/volc-credentials.md`、`setup_api.py`、`SetupView.vue`、`i18n.ts`、`builtin.py`。

## 1. 逐步走查

| # | 步骤 | 用户要做什么 | RUI 做什么 | 失败模式 / 用户看到什么 | UI/API/证据一致吗 |
|---|---|---|---|---|---|
| 0 | 冷装 | `curl …/install.sh \| sh`，准备 Docker 权限与可写目录 | 拉 compose/config、跑 `setup`、生成 `.env`、下模型、`compose up`、等 `/health` | Docker 权限、raw 超时、镜像拉取失败、模型下载失败、端口占用、容器名冲突 | ⚠️ 多处：见 §3 |
| 1 | keys | 每个用途选一个 provider，填空缺的凭证；TTS 选音色；先保存再测试 | 写 `secrets.yaml`；把 `providers.<stage>.chain` 改成 `[selected]`；TTS 写 voice | 默认 TTS 是讯飞三件套；只填火山 Key 的用户会卡在“缺失”；本地 ASR 点“测试”必红 | ❌ 文案说 ASR/TTS 不必都配，服务端却要求三个 stage 都 satisfied（`setup_api.py:259`） |
| 2 | models | 自己运行下载命令，再点“重新检查” | 只检查 ASR/VAD/KWS 文件，不代下 | 安装脚本漏下 VAD 时，此步永远 pending，UI 不给命令 | ⚠️ 证据正确，但“怎么做”要离开页面查 docs（`i18n.ts:484`） |
| 3 | prepare | 在音箱上装 client、配 URL/token、重启、看日志，勾选 7 项 | 展示 manifest 的链接/版本/步骤/回滚，不执行设备命令 | 清单第 4/6 项要求用 `connect` 步骤的 token/按钮，但 `connect` 在 3 步之后 | ❌ 顺序自相矛盾，诚实用户无法完成勾选（`builtin.py:49-54`） |
| 4 | params | 填地址、房间、唤醒词，保存 | 合并写 `config.yaml`，重启音频面；新设备 LED 默认开 | 文案说可调音量/灯效/端口/麦克风增益，表单其实只有 host/room/wake；音量=60、LED=on 是隐藏默认 | ❌ 文案与 manifest 冲突（`i18n.ts:522,524` vs `builtin.py:66-87`） |
| 5 | wake | 再确认/修改 1–3 个唤醒词，保存 | 重写 `keywords.txt`，再次重启音频面 | 与 params 重复输入；重启后 client 需重新回连 | ⚠️ 可用但冗余 |
| 6 | connect | 复制 ws 地址/token 到 client，等回连 | 只读展示；`connect.inspect` 不写配置、不做探测 | URL host 取自浏览器 Host：用 localhost/SSH 隧道打开时给出 `ws://127.0.0.1:4412`，永远连不上；TLS 反代下给 `wss://`，LX06 client 未必支持 | ❌ “测试连接”是空操作（`setup_api.py:923-924`） |
| 7 | test | 点“运行设备测试” | 探测 SSH 22 + 读 plane 的连接状态 | plane 未连但 SSH 通时整体 `ok=True`，显示绿色“通过” | ❌ 通过条件包含无关的 SSH（`setup_api.py:934`） |
| 8 | verify | 输入一句话，或对音箱说唤醒词；可跳过/人工确认 | 跑真实文本 turn，`audio_bytes>0` 即算完成 | `audio_bytes` 只证明 TTS 生成了音频，不证明音箱出声；跳过只在内存，刷新回 pending | ⚠️ 可选步，语义偏弱（`setup_api.py:490`、`SetupView.vue:2071`） |

## 2. UX 发现（P0/P1/P2）

**P0**

1. **prepare 清单把后续步骤当成前置条件。** 第 4 项要求把 `connect` 步骤给的 token 填进 client，第 6 项要求“回到 connect 步骤点重试”，但 `connect` 排在第 6 位、`params` 还没保存。提交按钮又要求 7 项全勾（`SetupView.vue:1945-1959`、`i18n.ts:505-511`），用户只能“假装勾完”。应把 prepare 拆成“设备侧准备”和“接线确认”，后者并到 connect，或把 connect 前移。
2. **控制台外壳在音箱真正连上之前就停止引导。** `App.vue:135-136` 用旧 `/api/v1/setup` 的 `complete`，而它是 keys/models/device/wake 四步（`app.py:3234`）。用户刚保存完唤醒词，刷新首页就不再跳 `/setup`，但新 8 步里 connect/test 还没做。品牌新用户会被直接丢进一个“设备离线”的总览页。

**P1**

3. **keys 完成度与文案/文档冲突。** 文案反复说“TTS/ASR 不要求都配”（`i18n.ts:428,459`），但 `keys_done` 要求 llm/asr/tts 全部满足（`setup_api.py:259`）；`docs/deploy.md:234-236` 又说“没有讯飞 key 就留空，自动落到火山”，实际 `chain[0]` 仍是 `xfyun_tts`，keys 永远不绿（`keycheck.py:410-423`）。用户必须自己发现下拉并保存切换。
4. **“测试”按钮的语义不可靠。** (a) 只测“已保存”的凭证，刚输入未保存就点会得到红色“未通过”（`i18n.ts:473`）；(b) 本地 ASR 无 key，后端返回 `ok:false` + “no safe online test”（`keycheck.py:566-568`），前端一律映射成红色 fail（`SetupView.vue:1861-1868`）；(c) 下拉切到不在当前 chain 的 provider（如 ASR 选 `volc_asr`）后直接点测试，会得到“测试接口尚未就绪（unknown_provider）”（`keycheck.py:527-533`、`SetupView.vue:1913-1923`），实际只是“先保存”。
5. **params 文案承诺了不存在的控件。** 用户读到的“音量、灯效、监听端口、麦克风增益”（`i18n.ts:522,524`）在 LX06 manifest 里根本没有；同一张卡片又写“没展示的字段会保留”（`i18n.ts:550`）。隐藏默认值与文档相反：新设备 LED 被强制打开（`setup_api.py:869-874`），文档仍写 `enabled: false`（`docs/deploy.md:118`）；schema 音量默认 60（`schema.py:130`），docs 写 0，设备页文案又写 0–255（`i18n.ts:676`）。
6. **connect 的 URL 不是“服务端生成”。** 它由 `request_console_host()` 取 `Host`/`X-Forwarded-Host` 拼出（`app.py:1088-1101,3580-3584`），但卡片写“这些值由服务端生成”（`i18n.ts:581`）。通过 `127.0.0.1:8080`、SSH 隧道或 TLS 反代访问时，给出的地址对音箱不可用；状态只显示“未连接，检查 token/端口”，没有“地址必须用 NAS 局域网 IP”的提示。
7. **connect 的“测试连接”是空动作。** `connect.inspect` 服务端 `pass`（`setup_api.py:923-924`），前端只是重新拉一次状态（`SetupView.vue:1979-1981`）；test 步骤又放了同名按钮并调用同一个 `inspectConnect`（`SetupView.vue:2885`）。真正会探测的是“运行设备测试”，三个名字都叫“测试”，用户无法预期。
8. **test 可以用 SSH 通过掩盖断连。** `ok = probe.usable or plane.speaker_connected`（`setup_api.py:934`），SSH banner 正常就绿色通过，即使音箱从未回连；而 `connect` 仍是 pending。test 的“通过”不应独立于 plane 连接成立（inbound）。
9. **安装脚本不可重跑/不可修复。** 第 5 步检查任何名为 `rui`/`house-ear` 的容器（含运行中）就 `exit 1`（`install.sh:164-172`）。一次成功安装后再跑同一脚本会直接报“another install”，与文档“幂等、已有文件不覆盖”相悖；升级/修配置只能手工 compose。
10. **固定版本的源码回退会偷偷构建 main。** 拉取失败时 `git clone --depth 1 https://github.com/$REPO.git` 没有 `--branch "$REF"`，然后 `docker build -t "$IMAGE"`（`install.sh:55-58`），于是 `RUI_REF=v0.2.0` 的“固定发布”实际得到 main 源码镜像，且打上 0.2.0 tag。`README.md:122` 仍承诺“builds the same tag from public source”。
11. **模型安装的跳过条件漏了 VAD。** 只要 ASR 与 KWS 在就打印“models already present”（`install.sh:132-134`），不检查 `silero_vad.onnx`；VAD 缺失时 install 不补，wizard 的 models 步永远红。`docs/deploy.md:160` 明确要求 VAD 存在。
12. **不健康也返回成功。** 120 秒探测失败只 `WARNING`，随后仍打印 “done. open …”，退出码 0（`install.sh:203-216`）。CI/脚本会把坏安装判成成功。
13. **models 步没有可执行指引。** 只显示“模型不在这步下载”（`i18n.ts:484`），没有命令、没有 `models_command`（旧 `/setup` 反而有，`app.py` 旧状态里带 `rui fetch-models …`）。新用户必须去翻 deploy 文档。
14. **文档/脚本仍教用户手改 YAML。** `docs/deploy.md:98-119` 让用户“加 devices[]、填 .env”，`install.sh:94` 也打印“next: add devices[] …”，与 README 的“打开 /setup 按向导完成”（`README.md:138`）是两条路；部署文档没提 `/setup` 的 8 步，README 的向导摘要也漏了 prepare/connect/test。

**P2**

15. **verify 的证据弱且不可持久跳过。** `audio_bytes>0` 只说明 TTS 产出（`setup_api.py:490`），“我听到了回答”是自我声明；跳过只改前端 `verifySkipped`（`SetupView.vue:2071-2078`），刷新后回 pending。设计稿说 verify 由 `/setup/try` 写 `first_answer`，实现是“setup 开始后的任意 turn”（`docs/install-wizard-design.md:134` vs `setup_api.py:217-230`），一次未连音箱的文本测试也能把它变绿。
16. **设计稿与实现多处漂移。** 设计说 prepare 在 test 通过后自动 done（`docs/install-wizard-design.md:68`），实现只认手动确认（`setup_api.py:333`）；设计列的 `/setup/try`、`/setup/test-connection` 端点不存在，实际统一走 `/setup/step`。
17. **密钥输出与提示。** 控制台 token 由 `rui setup` 在 `[2/5]` 中间打印，安装结尾不复述、不落文件；docs 提醒贴日志前脱敏（`docs/deploy.md:38-39`）。`app.py:3602` 的 hint 说“重新运行安装向导生成 token”，但向导从不生成 device token，与设计决策 #6 和 UI 文案（`i18n.ts:345`）矛盾。
18. **文档/版本小漂移。** README badge 与固定版本示例还是 0.2.0（`README.md:11,105`），HEAD 是 0.2.1；`docs/deploy.md:251` 仍把“没设 `web.auth_token`”列为可接受的 degraded，而 `rui setup` 会生成 token、非 loopback 空 token 直接拒绝启动（`__main__.py:105-108,177-187`）；`docs/open-xiaoai-client.md:22` 的 “Setup → Speaker” 已不存在（步骤叫 connect）。
19. **次要安装器问题。** 端口冲突只警告不中止（`install.sh:177-184`）；`ss` 不存在时完全跳过；`hostname -I` 在部分 NAS 返回空或容器网桥地址，会打印错误 URL（`install.sh:207`）；`keys.last_test` 只存最后一次、刷新后 UI 丢失（`setup_api.py:271-282`）。

## 3. 安装脚本对照审计

| 主题 | 文档承诺 | 脚本实际 | 结论 |
|---|---|---|---|
| 前置检查 | Docker 权限、可写目录（`docs/deploy.md:15-19`） | 检查 docker/daemon/mkdir；不检查 compose 插件、git、磁盘、host network | ⚠️ 缺 compose/git/磁盘预检 |
| 镜像 | 拉取失败“从公开源码构建同一 tag”（`README.md:122`） | 本地已有不拉；失败 clone 默认分支构建（`install.sh:49-60`） | ❌ 固定版本失效 |
| 配置/token | `setup` 写模板、生成 console token（`docs/deploy.md:28`） | 跑 `rui setup`，token 打到 stdout；`.env` 只生成 device token 并只打印长度（`install.sh:101-122`） | ✅ 行为正确；❌ 输出易被刷走/无复述 |
| 模型 | seed 复制 VAD/KWS + 下 ASR（`docs/deploy.md:148-165`） | 仅当 ASR+KWS 缺才跑` fetch-models`，忽略 VAD（`install.sh:132-150`） | ❌ 漏 VAD |
| 容器/端口 | 固定容器名、host network | 冲突即退出；端口只警告（`install.sh:164-184`） | ❌ 重跑失败；端口问题延后到运行期 |
| 健康 | 等 `/health`（`README.md:120`） | 120s 后只警告，仍打印 done（`install.sh:203-216`） | ❌ 退出码误导 |

## 4. 0.2.1 已修 vs 仍开放

**已修（`0a5446a`）**：provider 表（use → provider → credentials → voice）与 `providers.save`（`setup_api.py:693-789`）；真实 key 烟测（`keycheck.py:545+`）；connect 只读、不再写配置（`setup_api.py:923-924`）；新设备 LED 默认开（`setup_api.py:869-874`）；`chain[0]` 单 provider 完成度（`keycheck.py:410-423`）。
**仍开放**：prepare 顺序、外壳 `complete` 双实现、keys 文案/完成度冲突、test 语义、params 缺控件与默认值、connect URL/空动作、安装脚本重跑/固定版本/VAD/健康退出码、文档漂移。

## 5. 下一步（含验收标准）

1. **修 prepare 顺序**：把 token/connect 相关项从 prepare 移到 connect；或 prepare 只保留“装 client/留回滚”，connect 合并“填 URL + 等待 + 确认”。文件 `builtin.py:43-56`、`SetupView.vue:1945-1959`。验收：新用户不勾任何“未来步骤”也能完成 prepare。
2. **统一 complete**：`App.vue` 改用 `/api/v1/setup/step` 的 `complete`，或让旧 `/api/v1/setup` 的 `complete` 也要求 connect/test。文件 `App.vue:135-136`、`app.py:3234`。验收：connect 未 done 时打开 `/` 必跳 `/setup`。
3. **keys 文案与测试语义**：本地/无 key provider 显示中性“无需在线测试”；未保存时提示“先保存”；不在 chain 的 provider 测试给明确错误；文案改成“每个用途必须有一个可用 provider（本地 ASR 免密钥）”。文件 `SetupView.vue:1861-1923`、`i18n.ts:428,459,473`、`keycheck.py:527-568`。验收：默认本地 ASR 行不再出现红色失败。
4. **params 对齐**：要么在 manifest 增加 volume/LED/listen_port 并可编辑，要么删掉文案中的这些词；同步 `docs/deploy.md:114-119` 与 `schema.py:130` 的默认值。验收：文案提到的每个参数都能在表单找到，或明确说明在“设备”页。
5. **connect 真实化**：`connect.inspect` 改为真正的 plane/token 检查或直接删按钮；test 步骤只保留一个“测试”；URL host 若为 loopback/回环，显示警告并要求改用 LAN IP。文件 `setup_api.py:923-924`、`SetupView.vue:1979-1981,2846,2885`、`app.py:1088-1101`。验收：loopback 访问时 connect 卡给出可操作提示；不存在“点了没反应”的测试按钮。
6. **test 证据收紧**：inbound 的 `ok` 只认 `speaker_connected`，SSH 只作为诊断项。文件 `setup_api.py:934`。验收：SSH 通但未回连时 test 显示“未通过”，并指向 connect。
7. **安装脚本加固**：重跑时若容器属于本目录 compose project 则 `up -d` 而不是退出；`git clone --branch "$REF"`；模型条件加入 VAD；健康失败 `exit 1`。文件 `install.sh:55-58,132-134,164-172,203-216`。验收：二次运行成功；删 VAD 后重跑会补；固定 tag 的回退源码版本一致；不健康返回非 0。
8. **文档收口**：deploy 冷装改为“install.sh → /setup 八步”，删掉手改 YAML 主路径；更新 LED/音量/降级说明/版本号。文件 `docs/deploy.md:98-119,234-236,251`、`README.md:105,122,138`。验收：按 docs 从零走一遍不需要读源码或猜测。

## 6. 新用户验收脚本（在一台干净 Docker 主机上）

```bash
# 0) 期望：无 ERROR；[2/5] 打印 console token，[5/5] 打印 healthy
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
cd ~/rui && docker compose ps                      # 期望 State=running, Health=healthy
curl -fsS http://127.0.0.1:8080/health | jq '{status,audio_plane}'

# 1) 取 token，确认 setup 初始状态（期望 next_step=keys，8 个 step）
TOKEN=$(docker compose exec -T rui rui token)
H="X-Auth-Token: $TOKEN"
curl -fsS -H "$H" localhost:8080/api/v1/setup/step \
  | jq '{next_step,complete,steps:[.steps[]|{id,status}]}'

# 2) 浏览器打开 http://<NAS-LAN-IP>:8080/setup（不要用 localhost）
#    期望：keys 默认 TTS=讯飞、ASR=本地；不填讯飞三件套时 keys 不会变绿。
#    按提示保存后 GET 上一条，期望 keys.status=done。

# 3) 模型缺失时，按文档命令补，再点“重新检查”；镜像用 .env 里实际安装的版本
IMAGE=$(sed -n 's/^RUI_IMAGE=//p' ~/rui/.env)
docker run --rm --user "$(id -u):$(id -g)" -v ~/rui/data:/data "$IMAGE" \
  fetch-models --dest /data/models/sensevoice \
  --vad-dest /data/models/silero_vad.onnx --kws-dest /data/models/kws
#    期望：models.status=done（ASR/VAD/KWS 三项均为 true）。

# 4) prepare：尝试只按本步骤真实可做的 5 项勾选
#    期望（当前会失败）：第 4/6 项要求 connect 的 token/按钮，用户无法诚实完成。

# 5) params 保存后取连接信息，检查 URL host 是否为 LAN IP（不是 127.0.0.1 / localhost）
curl -fsS -H "$H" localhost:8080/api/v1/devices/living/connect \
  | jq '{configured,token_env,ws_url}'

# 6) 把 ws_url 写进音箱 client 并重启；轮询直到 connect done
while :; do
  curl -fsS -H "$H" localhost:8080/api/v1/setup/step \
    | jq -r '.steps[]|select(.id=="connect")|.status' | grep -q done && break
  sleep 3
done
#    期望：connected 后 connect=done、test=done（不应在 SSH 通但未回连时变 done）。

# 7) verify：输入“现在几点”，音箱应真的出声
curl -fsS -H "$H" -H 'Content-Type: application/json' \
  -d '{"step":"verify","action":"try.run","values":{"text":"现在几点"}}' \
  localhost:8080/api/v1/setup/step | jq '.steps[]|select(.id=="verify")|{status,evidence}'
#    期望：verify=done 且 audio_bytes>0；同时人耳确认音箱出声。

# 8) 回归检查（当前应暴露问题）
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
#    期望：同一安装可安全重跑（up -d），当前会报 container 'rui' already exists。
```
