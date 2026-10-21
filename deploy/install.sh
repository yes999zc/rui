#!/usr/bin/env sh
# One command: prepare config, pull the image, start the service.
set -eu

DIR="${HOUSE_EAR_HOME:-$HOME/house-ear}"
mkdir -p "$DIR/config" "$DIR/data"

if [ ! -f "$DIR/config/config.yaml" ]; then
  echo "[1/3] writing default config"
  docker run --rm -v "$DIR/config:/config" ghcr.io/OWNER/house-ear:latest \
    setup --config /config/config.yaml || true
else
  echo "[1/3] keeping existing config"
fi

if [ ! -f "$DIR/.env" ]; then
  echo "[2/3] writing .env template (fill in your keys)"
  cat > "$DIR/.env" <<'ENV'
HOUSE_EAR_LLM_KEY=
HOUSE_EAR_ASR_KEY=
HOUSE_EAR_TTS_KEY=
HOUSE_EAR_HA_TOKEN=
ENV
fi

echo "[3/3] starting"
cd "$DIR"
docker compose up -d

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "done. open http://${IP:-127.0.0.1}:8080/ and run: docker compose exec house-ear house-ear doctor"
