#!/usr/bin/env sh
# One command: lay down compose + config + env, then start the service.
set -eu

REPO="${HOUSE_EAR_REPO:-yes999zc/house-ear}"
REF="${HOUSE_EAR_REF:-main}"
IMAGE="${HOUSE_EAR_IMAGE:-ghcr.io/$REPO:latest}"
DIR="${HOUSE_EAR_HOME:-$HOME/house-ear}"
RAW="https://raw.githubusercontent.com/$REPO/$REF"

mkdir -p "$DIR/config" "$DIR/data"

echo "[1/5] compose file"
# The deploy directory is never a git checkout: fetch the one file compose needs,
# otherwise `docker compose up` fails with "no configuration file provided".
if [ ! -f "$DIR/docker-compose.yml" ]; then
  curl -fsSL "$RAW/docker-compose.yml" -o "$DIR/docker-compose.yml"
else
  echo "      keeping existing docker-compose.yml"
fi

echo "[2/5] config"
if [ ! -f "$DIR/config/config.yaml" ]; then
  # `setup` writes schema defaults: no devices, so a fresh install never shows a
  # speaker that does not exist. Add your own under devices[] afterwards.
  docker run --rm -v "$DIR/config:/config" "$IMAGE" setup --config /config/config.yaml
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

echo "[4/5] local ASR model"
# 239 MB, fetched once into the mounted data volume. Without it the ASR chain silently
# falls back to the cloud entry, which is slower and has a longer tail, so this step is
# loud when it fails but never blocks the install.
if [ -f "$DIR/data/models/sensevoice/model.int8.onnx" ]; then
  echo "      model already present"
else
  docker run --rm -v "$DIR/data:/data" "$IMAGE" fetch-models --dest /data/models/sensevoice \
    || echo "      WARNING: model download failed; ASR will use the cloud provider"
fi

echo "[5/5] starting"
cd "$DIR"
HOUSE_EAR_IMAGE="$IMAGE" docker compose up -d

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "done. open http://${IP:-127.0.0.1}:8080/ then run: docker compose exec house-ear house-ear doctor"
echo "fill in the provider keys in $DIR/.env and restart to use real ASR/TTS/LLM"
