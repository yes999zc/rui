# arm64 运行验证：`ghcr.io/yes999zc/rui:0.8.5`（linux/arm64）

> 状态：**通过（运行时层）**。验证日期：2026-10-29。
> 宿主：Apple Silicon Mac（arm64），本机 Docker daemon；容器平台 `linux/arm64`。
> 边界：不部署生产、不碰 NAS、不改代码、不改 `config.example.yaml`、不加依赖。
> 镜像为公开包，本次无需 GHCR 认证（未使用、未写入任何 token）。

## 1. 结论

在 Apple Silicon Mac 上用原生 `linux/arm64` 容器验证，`rui:0.8.5` 的 arm64 镜像**可以运行**：

| 验证层 | 口径 | 结果 |
|---|---|---|
| 镜像可拉取 | `docker pull --platform linux/arm64` | ✅ 成功，arm64 manifest digest 见 §2 |
| 二进制可执行 | `ffmpeg -version` | ✅ `ffmpeg version 7.1.5` |
| Python 依赖可导入 | `import rui, numpy` | ✅ `0.8.5 2.5.4` |
| 服务可启动 | `rui serve --config /data/config.yaml` | ✅ uvicorn 启动完成，容器内监听 `:8080` |
| 探活 | `GET /health` | ✅ **HTTP 200**，body `"version":"0.8.5"` |
| 清理 | `docker stop` / `docker rm` | ✅ 均 exit 0，无残留容器 |

结论：0.8.5 的 arm64 镜像具备在 ARM Linux 主机上运行的最低条件（二进制、Python/numpy 依赖、
服务与 `/health` 探活），而不只是 amd64 镜像能在 ARM 上通过 emulation 跑起来——本次强制
`--platform linux/arm64`，`docker image inspect` 也确认本地镜像为 `linux/arm64`。

## 2. 镜像与平台

| 项 | 值 |
|---|---|
| tag | `ghcr.io/yes999zc/rui:0.8.5` |
| index digest | `sha256:20347039ee559db4e07989f9311c4fec78461163c6ce23312867e5350c51d383` |
| linux/arm64 manifest | `sha256:c625d5c1a7469b1ab39163dbcbd2574cb5dd62e36a8cae05d4c556e00d2c8649` |
| linux/amd64 manifest | `sha256:4bfd73d5b8e0ffadd22512f888d922d6b3355041214f5d3cdaaf17d936bd50a1` |
| 本地 image ID | `sha256:3baf2b783f62ed68adab4b288542eb4c7283133840446f70bee41ff6148723c7` |
| 平台 | `linux/arm64`（`docker image inspect --format '{{.Os}}/{{.Architecture}}'`） |
| ENTRYPOINT / CMD | `["rui"]` / `["serve"]`（`User=rui`，`EXPOSE 8080`） |

钉住平台的可复现写法：

```bash
ghcr.io/yes999zc/rui:0.8.5@sha256:c625d5c1a7469b1ab39163dbcbd2574cb5dd62e36a8cae05d4c556e00d2c8649
```

## 3. 证据（完整命令 + exit code + 关键输出）

### 3.1 拉取 arm64 镜像

```console
$ docker pull --platform linux/arm64 ghcr.io/yes999zc/rui:0.8.5
...
Digest: sha256:20347039ee559db4e07989f9311c4fec78461163c6ce23312867e5350c51d383
Status: Downloaded newer image for ghcr.io/yes999zc/rui:0.8.5
EXIT=0

$ docker image inspect --platform linux/arm64 \
    --format '{{.Os}}/{{.Architecture}} {{.Id}}' ghcr.io/yes999zc/rui:0.8.5
linux/arm64 sha256:3baf2b783f62ed68adab4b288542eb4c7283133840446f70bee41ff6148723c7
EXIT=0
```

### 3.2 二进制可执行（ffmpeg）

镜像 `ENTRYPOINT ["rui"]`，所以字面上不加 `--entrypoint` 的写法会把 `ffmpeg` 当成 rui 的子命令：

```console
$ docker run --rm --platform linux/arm64 ghcr.io/yes999zc/rui:0.8.5 ffmpeg -version | head -1
usage: rui [-h] [--config CONFIG]
EXIT=2
```

实际等价命令（覆盖 entrypoint）：

```console
$ docker run --rm --platform linux/arm64 --entrypoint ffmpeg \
    ghcr.io/yes999zc/rui:0.8.5 -version | head -1
ffmpeg version 7.1.5 Copyright (c) 2000-2026 the FFmpeg developers
EXIT=0
```

### 3.3 Python + numpy 依赖可导入

同样原因，`python` 也要覆盖 entrypoint：

```console
$ docker run --rm --platform linux/arm64 ghcr.io/yes999zc/rui:0.8.5 \
    python -c "import rui, numpy; print(rui.__version__, numpy.__version__)"
rui: error: argument command: invalid choice: 'python' (...)
EXIT=2

$ docker run --rm --platform linux/arm64 --entrypoint python \
    ghcr.io/yes999zc/rui:0.8.5 -c "import rui, numpy; print(rui.__version__, numpy.__version__)"
0.8.5 2.5.4
EXIT=0
```

### 3.4 起服务并探活 `/health`

最小配置（`/data/config.yaml`，`devices: []`，不接真实音箱；`auth_token` 可为任意临时值）：

```yaml
version: 2
system:
  data_dir: /data/state
  log_level: info
web:
  host: 0.0.0.0
  port: 8080
  auth_token: "<temporary-token>"
  allow_anonymous: false
devices: []
```

启动（容器内仍监听 8080）：

```console
$ mkdir -p /tmp/rui-arm64/data
$ chmod -R 777 /tmp/rui-arm64      # 镜像以 uid 1000 运行；macOS bind mount 需可写
$ docker run -d --name rui-arm64 --platform linux/arm64 \
    -p 18081:8080 -v /tmp/rui-arm64:/data \
    ghcr.io/yes999zc/rui:0.8.5 serve --config /data/config.yaml
19a5a7ed49a031ff3c45868959f072ac6a838d38614eb42336004fef2f03b239

$ docker logs rui-arm64
INFO:     Started server process [1]
INFO:     Waiting for application startup.
...
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)

$ curl -s -o /tmp/arm-health.json -w '%{http_code}' http://127.0.0.1:18081/health
200
EXIT=0
```

`/health` body（截取关键字段）：

```json
{"status":"ok","version":"0.8.5","config_version":2,"config_version_supported":3,
 "console":"v2","uptime_s":1.6,"devices":0,"enabled_devices":0,"device_errors":0,
 "audio_plane":false,"planes":[],"warnings":[],"multi_device":{"enabled":false},
 "ack":{"mode":"earcon","note":""}}
```

清理：

```console
$ docker stop rui-arm64
rui-arm64
STOP_EXIT=0
$ docker rm rui-arm64
rui-arm64
RM_EXIT=0
```

注意（端口）：本次验证时宿主机 `127.0.0.1:18080` 已被一个**既有的本地 rui 0.7.2 进程**占用
（与本镜像无关，未触碰该进程），所以示例改用 `-p 18081:8080`；端口号不影响结论，宿主端口空闲时
`-p 18080:8080` + `curl 127.0.0.1:18080/health` 等价。

运行日志中出现的两类预期告警（不影响 `/health` 200）：

- `asr/llm/tts: no ready provider - missing credential: RUI_ASR_KEY / RUI_LLM_KEY / RUI_TTS_KEY`
  —— 最小配置未配 provider 凭证，符合预期；
- `audio plane did not start: no device this build can drive` —— Mac 宿主无音箱、镜像也不含
  `rui-xiaoai-bridge` plug，符合预期。

## 4. 未验证项（明确不在本次范围内）

- **ARM 上多设备/音频平面的性能**：未做并发、时延、CPU/内存测量（本卡明确不做性能与基准）。
- **长时间稳定性**：只验证了启动后数十秒内的探活，未做小时级 soak。
- **真实音频设备与真实音箱链路**：宿主无扬声器/麦克风，镜像不含 `rui-xiaoai-bridge` plug，
  `devices: []`；AEC、KWS、ASR/TTS 实链路未验证。
- **真实 provider 链路**：无 ASR/LLM/TTS 凭证，仅验证 provider 缺失时服务仍能启动并探活。
- **Docker HEALTHCHECK 的最终状态**：只观察了 `/health` 代理探测返回 200；镜像内置
  `HEALTHCHECK` 在 `start-period 90s` 内未等待其变为 `healthy`。
- **生产 compose 形态**（`read_only`/`tmpfs`/`user:` 映射/`network_mode: host`）：本次用裸
  `docker run` 验证运行时，未验证 compose 部署路径。

## 5. 复现命令（一次跑完）

```bash
IMG=ghcr.io/yes999zc/rui:0.8.5
# 或钉平台：IMG=ghcr.io/yes999zc/rui:0.8.5@sha256:c625d5c1a7469b1ab39163dbcbd2574cb5dd62e36a8cae05d4c556e00d2c8649

docker pull --platform linux/arm64 "$IMG"
docker image inspect --platform linux/arm64 --format '{{.Os}}/{{.Architecture}} {{.Id}}' "$IMG"
docker run --rm --platform linux/arm64 --entrypoint ffmpeg "$IMG" -version | head -1
docker run --rm --platform linux/arm64 --entrypoint python "$IMG" \
  -c "import rui, numpy; print(rui.__version__, numpy.__version__)"

mkdir -p /tmp/rui-arm64/data && chmod -R 777 /tmp/rui-arm64
cat > /tmp/rui-arm64/config.yaml <<'YAML'
version: 2
system:
  data_dir: /data/state
  log_level: info
web:
  host: 0.0.0.0
  port: 8080
  auth_token: "arm64-verify-token"
  allow_anonymous: false
devices: []
YAML
docker run -d --name rui-arm64 --platform linux/arm64 \
  -p 18081:8080 -v /tmp/rui-arm64:/data "$IMG" serve --config /data/config.yaml
sleep 8
curl -s -o /tmp/arm-health.json -w '%{http_code}\n' http://127.0.0.1:18081/health
grep -o '"version":"[^"]*"' /tmp/arm-health.json   # 期望 "version":"0.8.5"
docker stop rui-arm64 && docker rm rui-arm64
```
