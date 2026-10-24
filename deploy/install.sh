#!/usr/bin/env sh
# One command: lay down compose + config + env, then start the service.
set -eu

REPO="${RUI_REPO:-${HOUSE_EAR_REPO:-yes999zc/rui}}"
REF="${RUI_REF:-${HOUSE_EAR_REF:-main}}"
IMAGE="${RUI_IMAGE:-${HOUSE_EAR_IMAGE:-ghcr.io/$REPO:latest}}"
DIR="${RUI_HOME:-${HOUSE_EAR_HOME:-$HOME/rui}}"
RAW="${RUI_RAW:-${HOUSE_EAR_RAW:-https://raw.githubusercontent.com/$REPO/$REF}}"
# Every fetch below is bounded: on a mainland network raw.githubusercontent.com can
# accept the TCP connection and then stall forever, and this script used to hang at
# step [1/5] with no output (seen 2026-10-24). A timeout turns that into a visible
# warning instead of an installer that never finishes.
# -4: hosts without a global IPv6 route get an unusable AAAA first answer; forcing
# IPv4 removes one more way for a single fetch to stall.
CURL="curl -4 -fsSL --connect-timeout 10 --max-time 120 --retry 3 --retry-all-errors"
# Created by this script, so the files belong to the person who ran it, not to root.
# The service container itself still runs as root (it writes its own data volume).
RUN_AS="$(id -u):$(id -g)"

# Fail before any work: the usual cold-install failures are about the shell running this
# script, not about the project. Catching them here beats a cryptic permission error
# halfway through a model download.
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: 'docker' is not installed or not in PATH." >&2
  echo "       install Docker Engine + the compose plugin first, then re-run this script." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: cannot talk to the Docker daemon (docker info failed)." >&2
  echo "       make sure Docker is running; run as root, or add this user to the docker" >&2
  echo "       group and log in again:" >&2
  echo '         sudo usermod -aG docker "$USER"' >&2
  exit 1
fi

# The installer writes as the invoking user. NAS mounts such as /vol1 are often root-only;
# fail with a readable message instead of leaving a half-created install directory.
if ! mkdir -p "$DIR/config" "$DIR/data/models"; then
  echo "ERROR: cannot create $DIR." >&2
  echo "       use a writable RUI_HOME, run the installer as root, or create the directory" >&2
  echo "       first and chown it to this user." >&2
  exit 1
fi

# A private GHCR package (or an offline registry) must not make the one-command install
# fail forever. If the image cannot be pulled, build the same tag from the public source;
# a later re-run reuses the image, and once the package is public the fast path returns.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  if ! PULL_OUTPUT="$(docker pull "$IMAGE" 2>&1)"; then
    echo "WARNING: could not pull $IMAGE; falling back to a source build." >&2
    printf '%s\n' "$PULL_OUTPUT" | sed 's/^/         /' >&2
    echo "[0/5] building the image from the public source (first run only)"
    SRC="$DIR/src"
    if [ ! -d "$SRC/.git" ]; then
      git clone --depth 1 "https://github.com/$REPO.git" "$SRC"
    fi
    docker build -t "$IMAGE" "$SRC"
  fi
fi

echo "[1/5] compose file"
# The deploy directory is never a git checkout: fetch the one file compose needs,
# otherwise `docker compose up` fails with "no configuration file provided". The example
# config comes along as the reference for the hand edits in step 2 (setup writes the
# production template; the example is the commented, annotated version of it).
if [ ! -f "$DIR/docker-compose.yml" ]; then
  $CURL "$RAW/docker-compose.yml" -o "$DIR/docker-compose.yml"
else
  echo "      keeping existing docker-compose.yml"
fi
$CURL "$RAW/config.example.yaml" -o "$DIR/config.example.yaml" \
  || echo "      WARNING: could not fetch config.example.yaml (optional reference)"

# Ops scripts live in the repo, not in the image. Fetch the backup/restore pair and the
# guided acceptance recorder next to the compose file so docs/runbook.md commands work
# after a cold install; a failed fetch must not block the service (best effort).
mkdir -p "$DIR/scripts"
for f in backup.sh restore.sh acceptance-v1.py; do
  if [ ! -f "$DIR/scripts/$f" ]; then
    $CURL "$RAW/scripts/$f" -o "$DIR/scripts/$f" \
      && chmod 755 "$DIR/scripts/$f" \
      || echo "      WARNING: could not fetch scripts/$f (backup/restore docs will need a manual copy)"
  fi
done

echo "[2/5] config + wake words"
if [ ! -f "$DIR/config/config.yaml" ]; then
  # `setup` writes the production template (provider chains, model paths, ack TTS) and
  # generates keywords.txt from the default wake word, so the only things left by hand
  # are devices[] and the keys in .env. It still writes no devices: a fresh install must
  # not show a speaker that does not exist.
  docker run --rm -e RUI_INSTALLER=1 --user "$RUN_AS" -v "$DIR/config:/config" "$IMAGE" setup --config /config/config.yaml
  echo "      next: add devices[] and set audio.voice.kws_keywords_file/wake_words to match"
  echo "      after changing wake words: docker run --rm -v $DIR/config:/config $IMAGE keywords --config /config/config.yaml"
  echo "      reference: $DIR/config.example.yaml and docs/deploy.md"
else
  echo "      keeping existing config.yaml"
fi

echo "[3/5] env"
if [ ! -f "$DIR/.env" ]; then
  # One random secret per install, never printed except as a length.
  DEVICE_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  cat > "$DIR/.env" <<ENV
RUI_LLM_KEY=
RUI_ASR_KEY=
VOLC_API_KEY=
XFYUN_APPID=
XFYUN_API_KEY=
XFYUN_API_SECRET=
RUI_HA_TOKEN=
RUI_DEVICE_TOKEN=$DEVICE_TOKEN
# Pin the image compose uses, so a later plain `docker compose up -d` (or a reboot)
# keeps the version that was installed instead of falling back to :latest.
RUI_IMAGE=$IMAGE
ENV
  chmod 600 "$DIR/.env"
  echo "      wrote .env with a generated device token (${#DEVICE_TOKEN} hex chars)"
else
  echo "      keeping existing .env"
fi

echo "[4/5] local models: ASR + VAD + wake word"
# Fresh images ship Silero VAD and the KWS wake model at /opt/seed-models. fetch-models
# copies those two out before its first network request, so a cold install only downloads
# SenseVoice (239 MB, hf-mirror) - the 33 MB GitHub release asset that used to stall is
# already in the image. An older image without /opt/seed-models falls back to the previous
# download path automatically; no flag needed here. Without ASR the chain silently falls
# back to cloud; without VAD the gate never starts; without KWS the box cannot hear its
# own wake word. Loud when it fails, but never blocks the install.
KWS_TOKENS="$DIR/data/models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/tokens.txt"
if [ -f "$DIR/data/models/sensevoice/model.int8.onnx" ] && [ -f "$KWS_TOKENS" ]; then
  echo "      models already present"
else
  # A host proxy is the difference between a 2 MB/s download and a 55 KB/s one for the
  # GitHub release assets (measured 2026-10-24: direct 55 KB/s, mihomo 2.1 MB/s). The
  # container does not inherit the host environment, so forward the usual proxy vars
  # explicitly; host networking keeps 127.0.0.1 proxies reachable.
  FETCH_PROXY=""
  for var in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy; do
    eval "value=\${$var:-}"
    [ -n "$value" ] && FETCH_PROXY="$FETCH_PROXY -e $var=$value"
  done
  # shellcheck disable=SC2086  # FETCH_PROXY is a list of docker flags, not a path
  docker run --rm --network host --user "$RUN_AS" -v "$DIR/data:/data" $FETCH_PROXY "$IMAGE" \
      fetch-models --dest /data/models/sensevoice \
      --vad-dest /data/models/silero_vad.onnx \
      --kws-dest /data/models/kws \
    || echo "      WARNING: model download failed; ASR falls back to the cloud, wake word stays off (see docs/deploy.md)"
fi
# setup already generated config/keywords.txt from the default wake word; only a change
# of devices[].wake_words needs a regeneration.
if [ ! -f "$DIR/config/keywords.txt" ]; then
  echo "      NOTE: config/keywords.txt missing (old install?): run the keywords command from docs/deploy.md"
fi

echo "[5/5] starting"
# A second install on the same host would collide on the fixed container name. 0.1.x used
# `house-ear`, 0.2.0 uses `rui`, and either container with --network host keeps :8080/:4412
# after its compose project is gone - so check both names instead of only `rui`. Fail with
# an actionable message rather than a raw Docker "Conflict" (seen 2026-10-24 while testing
# a cold install next to a running production instance).
for name in rui house-ear; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    echo "ERROR: a container named '$name' already exists (another Rui/house-ear install)."
    echo "       stop and remove it before installing here:"
    echo "         docker stop $name && docker rm $name"
    echo "       or, from that install's directory: docker compose down"
    exit 1
  fi
done
cd "$DIR"
# --network host publishes no port mapping, so the name check above is the owner signal.
# If the port is still taken (an unrelated service or a manually run container), say so
# now instead of letting uvicorn fail during startup.
PORT="$(grep -E '^[[:space:]]*port:' "$DIR/config/config.yaml" 2>/dev/null | head -1 | awk '{print $2}')"
PORT="${PORT:-8080}"
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE "[:.]$PORT[[:space:]]"; then
  OWNER="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | awk -v p=":$PORT->" 'index($0, p) {print $1; exit}')"
  echo "WARNING: port $PORT is already in use${OWNER:+ by container '$OWNER'}."
  ss -ltnp 2>/dev/null | grep -E "[:.]$PORT[[:space:]]" | sed 's/^/         /' || true
  echo "         free it, or change web.port in $DIR/config/config.yaml, before starting."
fi
# Pass the compose file explicitly: it also pins the project directory, so a stray cwd
# can never make compose start the *other* install's project.
RUI_IMAGE="$IMAGE" docker compose -f "$DIR/docker-compose.yml" --project-directory "$DIR" up -d

# Wait for the service instead of printing a URL that may still 502. The probe is the
# image's own healthcheck: it reads web.port from config and also requires the audio
# plane when a device is configured (a running container with a dead plane answers
# /health but cannot hear anything).
echo "      waiting for the service to answer /health"
i=0
while [ "$i" -lt 120 ]; do
  if docker compose exec -T rui rui-healthcheck >/dev/null 2>&1; then
    echo "      healthy"
    break
  fi
  i=$((i + 1))
  sleep 1
done
if [ "$i" -ge 120 ]; then
  echo "      WARNING: not healthy after 120 s; inspect: docker compose logs --tail=100 rui"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "done. open http://${IP:-127.0.0.1}:${PORT}/"
echo "  doctor:  docker compose exec rui rui doctor"
echo "  logs:    docker compose logs -f rui"
echo "  keys:    open the console -> Setup -> Keys (one provider per use), or fill $DIR/.env"
echo "  speaker: open the console -> Setup -> Speaker connection to copy the ws URL + device token"
echo "           (step-by-step: docs/open-xiaoai-client.md)"
echo "  pin:     image is pinned in $DIR/.env ($IMAGE); edit that line to change version"
echo "  rollback: set the previous tag in $DIR/.env and run docker compose up -d"
