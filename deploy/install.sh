#!/usr/bin/env sh
# One command: lay down compose + config + env, then start the service.
set -eu

REPO="${HOUSE_EAR_REPO:-yes999zc/rui}"
REF="${HOUSE_EAR_REF:-main}"
IMAGE="${HOUSE_EAR_IMAGE:-ghcr.io/$REPO:latest}"
DIR="${HOUSE_EAR_HOME:-$HOME/house-ear}"
RAW="${HOUSE_EAR_RAW:-https://raw.githubusercontent.com/$REPO/$REF}"
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

mkdir -p "$DIR/config" "$DIR/data/models"

# A private GHCR package (or an offline registry) must not make the one-command install
# fail forever. If the image cannot be pulled, build the same tag from the public source;
# a later re-run reuses the image, and once the package is public the fast path returns.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  if ! docker pull "$IMAGE" >/dev/null 2>&1; then
    echo "[0/5] image not pullable; building it from the public source (first run only)"
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
  docker run --rm --user "$RUN_AS" -v "$DIR/config:/config" "$IMAGE" setup --config /config/config.yaml
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
HOUSE_EAR_LLM_KEY=
HOUSE_EAR_ASR_KEY=
VOLC_API_KEY=
XFYUN_APPID=
XFYUN_API_KEY=
XFYUN_API_SECRET=
HOUSE_EAR_HA_TOKEN=
HOUSE_EAR_DEVICE_TOKEN=$DEVICE_TOKEN
# Pin the image compose uses, so a later plain `docker compose up -d` (or a reboot)
# keeps the version that was installed instead of falling back to :latest.
HOUSE_EAR_IMAGE=$IMAGE
ENV
  chmod 600 "$DIR/.env"
  echo "      wrote .env with a generated device token (${#DEVICE_TOKEN} hex chars)"
else
  echo "      keeping existing .env"
fi

echo "[4/5] local models: ASR + VAD + wake word"
# ~272 MB fetched once into the mounted data volume: SenseVoice (239 MB), Silero VAD
# (0.6 MB) and the KWS wake model (32.9 MB tarball). Without ASR the chain silently
# falls back to cloud; without VAD the gate never starts; without KWS the box cannot
# hear its own wake word. Loud when it fails, but never blocks the install.
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
cd "$DIR"
HOUSE_EAR_IMAGE="$IMAGE" docker compose up -d

# Wait for the service instead of printing a URL that may still 502. The probe is the
# image's own healthcheck: it reads web.port from config and also requires the audio
# plane when a device is configured (a running container with a dead plane answers
# /health but cannot hear anything).
echo "      waiting for the service to answer /health"
i=0
while [ "$i" -lt 120 ]; do
  if docker compose exec -T house-ear house-ear-healthcheck >/dev/null 2>&1; then
    echo "      healthy"
    break
  fi
  i=$((i + 1))
  sleep 1
done
if [ "$i" -ge 120 ]; then
  echo "      WARNING: not healthy after 120 s; inspect: docker compose logs --tail=100 house-ear"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PORT="$(grep -E '^[[:space:]]*port:' "$DIR/config/config.yaml" 2>/dev/null | head -1 | awk '{print $2}')"
echo
echo "done. open http://${IP:-127.0.0.1}:${PORT:-8080}/"
echo "  doctor:  docker compose exec house-ear house-ear doctor"
echo "  logs:    docker compose logs -f house-ear"
echo "  keys:    fill in $DIR/.env, then: docker compose up -d"
echo "  speaker: open the console -> Setup -> Speaker connection to copy the ws URL + device token"
echo "           (step-by-step: docs/open-xiaoai-client.md)"
echo "  pin:     image is pinned in $DIR/.env ($IMAGE); edit that line to change version"
echo "  rollback: set the previous tag in $DIR/.env and run docker compose up -d"
