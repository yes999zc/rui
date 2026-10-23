#!/usr/bin/env sh
# One command: lay down compose + config + env, then start the service.
set -eu

REPO="${HOUSE_EAR_REPO:-yes999zc/house-ear}"
REF="${HOUSE_EAR_REF:-main}"
IMAGE="${HOUSE_EAR_IMAGE:-ghcr.io/$REPO:latest}"
DIR="${HOUSE_EAR_HOME:-$HOME/house-ear}"
RAW="https://raw.githubusercontent.com/$REPO/$REF"
# Created by this script, so the files belong to the person who ran it, not to root.
# The service container itself still runs as root (it writes its own data volume).
RUN_AS="$(id -u):$(id -g)"

mkdir -p "$DIR/config" "$DIR/data/models"

echo "[1/5] compose file"
# The deploy directory is never a git checkout: fetch the one file compose needs,
# otherwise `docker compose up` fails with "no configuration file provided". The example
# config comes along as the reference for the hand edits in step 2 (the generated config
# is schema defaults only: no devices, no provider endpoints).
if [ ! -f "$DIR/docker-compose.yml" ]; then
  curl -fsSL "$RAW/docker-compose.yml" -o "$DIR/docker-compose.yml"
else
  echo "      keeping existing docker-compose.yml"
fi
curl -fsSL "$RAW/config.example.yaml" -o "$DIR/config.example.yaml" \
  || echo "      WARNING: could not fetch config.example.yaml (optional reference)"

echo "[2/5] config"
if [ ! -f "$DIR/config/config.yaml" ]; then
  # `setup` writes schema defaults: no devices, so a fresh install never shows a
  # speaker that does not exist. Add your own under devices[] afterwards.
  docker run --rm --user "$RUN_AS" -v "$DIR/config:/config" "$IMAGE" setup --config /config/config.yaml
  echo "      next: edit $DIR/config/config.yaml (devices, providers, wake model)"
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
HOUSE_EAR_TTS_KEY=
HOUSE_EAR_HA_TOKEN=
HOUSE_EAR_DEVICE_TOKEN=$DEVICE_TOKEN
ENV
  chmod 600 "$DIR/.env"
  echo "      wrote .env with a generated device token (${#DEVICE_TOKEN} hex chars)"
else
  echo "      keeping existing .env"
fi

echo "[4/5] local ASR + VAD models"
# 239 MB, fetched once into the mounted data volume. Without it the ASR chain silently
# falls back to the cloud entry, which is slower and has a longer tail, so this step is
# loud when it fails but never blocks the install.
if [ -f "$DIR/data/models/sensevoice/model.int8.onnx" ]; then
  echo "      model already present"
else
  # Both models: without the VAD the turn gate never starts, and every S5a feature
  # (wake cue, pre-wake replay, gate metrics) is silently inert on a fresh install.
  docker run --rm --user "$RUN_AS" -v "$DIR/data:/data" "$IMAGE" \
      fetch-models --dest /data/models/sensevoice \
      --vad-dest /data/models/silero_vad.onnx \
    || echo "      WARNING: model download failed; ASR uses the cloud provider, the wake path stays off"
fi
# The generated config does not reference these paths (schema defaults); the example does.
# Point it out instead of editing YAML from shell: the paths only help if the provider
# block and the wake model are set up too.
if ! grep -q "/data/models/sensevoice" "$DIR/config/config.yaml" 2>/dev/null; then
  echo "      NOTE: config.yaml does not reference /data/models yet - see docs/deploy.md"
fi
# The wake word needs its own KWS model, which fetch-models does not download (it is a
# model directory plus a keywords file). Without it, audio.voice.kws_enabled must stay
# false and the box only answers turns it did not wake.

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
echo "  pin:     echo 'HOUSE_EAR_IMAGE=$IMAGE' >> $DIR/.env for a reproducible deploy"
echo "  rollback: set the previous tag in $DIR/.env and run docker compose up -d"
