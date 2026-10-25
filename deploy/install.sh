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
# The image runs unprivileged as 1000:1000. Bind mounts carry host ownership, so the
# installer (normally root on a NAS) chowns ./config and ./data to this pair and compose
# is given the same mapping. Override both together when 1000 is already taken on the
# host: compose `user:` replaces the image default, and /app stays world-readable, so no
# rebuild is needed. The one-off `docker run` helpers below use the same pair.
RUI_UID="${RUI_UID:-1000}"
RUI_GID="${RUI_GID:-1000}"
case "$RUI_UID" in ''|*[!0-9]*) echo "ERROR: RUI_UID must be numeric, got '$RUI_UID'." >&2; exit 1;; esac
case "$RUI_GID" in ''|*[!0-9]*) echo "ERROR: RUI_GID must be numeric, got '$RUI_GID'." >&2; exit 1;; esac
RUN_AS="$RUI_UID:$RUI_GID"

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
if ! mkdir -p "$DIR/config" "$DIR/data/models" "$DIR/data/state"; then
  echo "ERROR: cannot create $DIR." >&2
  echo "       use a writable RUI_HOME, run the installer as root, or create the directory" >&2
  echo "       first and chown it to this user." >&2
  exit 1
fi

# The container is non-root, so the bind-mounted config/data trees must be owned by the
# runtime uid/gid. This is also the upgrade path for installs created while the image ran
# as root: chown is idempotent and does not rewrite file contents. A non-root invoker can
# only do it when they already are that identity (or have CAP_CHOWN); otherwise fail with
# the exact command instead of starting a container that cannot write.
ensure_ownership() {
  if chown -R "$RUN_AS" "$DIR/config" "$DIR/data" 2>/dev/null; then
    return 0
  fi
  # uid is what matters for owner bits; the primary gid of the invoking shell does not
  # have to equal RUI_GID if the container user owns the files. A different gid is only
  # relevant for group-only access, so do not fail a working install on that alone.
  if [ "$(id -u)" = "$RUI_UID" ] \
      && [ -w "$DIR/config" ] && [ -w "$DIR/data" ] && [ -w "$DIR/data/models" ] \
      && [ -w "$DIR/data/state" ]; then
    return 0
  fi
  echo "ERROR: $DIR/config and $DIR/data are not writable by uid:gid $RUN_AS." >&2
  echo "       the container runs as $RUN_AS; fix ownership as root (or with sudo):" >&2
  echo "         sudo chown -R $RUN_AS \"$DIR/config\" \"$DIR/data\"" >&2
  echo "       if this host cannot use 1000:1000, re-run with this account's ids:" >&2
  echo "         RUI_UID=$(id -u) RUI_GID=$(id -g) sh install.sh" >&2
  echo "       (compose overrides the image's USER; the chown above must still succeed)." >&2
  exit 1
}

# A private GHCR package (or an offline registry) must not make the one-command install
# fail forever. If the image cannot be pulled, build the same tag from the public source;
# a later re-run reuses the image, and once the package is public the fast path returns.
# The clone/build must follow RUI_REF, not the default branch: a pinned install
# (RUI_REF=v0.2.1) that silently builds main would run untested code under a release tag.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  if ! PULL_OUTPUT="$(docker pull "$IMAGE" 2>&1)"; then
    echo "WARNING: could not pull $IMAGE; falling back to a source build." >&2
    printf '%s\n' "$PULL_OUTPUT" | sed 's/^/         /' >&2
    if ! command -v git >/dev/null 2>&1; then
      echo "ERROR: the source fallback needs 'git'." >&2
      echo "       install git, or pull the image manually and re-run this script." >&2
      exit 1
    fi
    SRC="$DIR/src"
    if [ ! -d "$SRC/.git" ]; then
      if [ -d "$SRC" ] && [ -n "$(ls -A "$SRC" 2>/dev/null || true)" ]; then
        echo "ERROR: $SRC exists but is not a git checkout; refusing to build unknown source." >&2
        echo "       move it aside (or remove it) and re-run this script." >&2
        exit 1
      fi
      echo "      cloning https://github.com/$REPO.git at ref '$REF'"
      git clone --depth 1 --branch "$REF" "https://github.com/$REPO.git" "$SRC"
    else
      echo "      updating $SRC to ref '$REF'"
      git -C "$SRC" fetch --depth 1 origin "$REF"
      git -C "$SRC" checkout --detach FETCH_HEAD
    fi
    BUILT_REV="$(git -C "$SRC" rev-parse --short HEAD)"
    echo "[0/5] building $IMAGE from $REPO@$REF (commit $BUILT_REV)"
    docker build -t "$IMAGE" "$SRC"
  fi
fi

echo "[1/5] compose file"
# The deploy directory is never a git checkout: fetch the one file compose needs,
# otherwise `docker compose up` fails with "no configuration file provided". The example
# config comes along as the commented reference for manual recovery; `setup` writes the
# production template, and the wizard owns devices[] on the normal path.
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

# Existing installs predate the non-root image: their config/ and data/ are root-owned,
# so this chown is the one-time upgrade step. Tell the operator before touching it and
# point at the backup path, since the chown is safe but the version change may not be.
if [ -f "$DIR/config/config.yaml" ]; then
  echo "      existing install: ensuring config/ and data/ are owned by $RUN_AS"
  echo "      backup before upgrading: $DIR/scripts/backup.sh (or cp -a $DIR/config $DIR/data)"
elif [ "$(id -u)" = "0" ]; then
  echo "      fresh install: config/ and data/ owned by $RUN_AS"
fi
ensure_ownership

echo "[2/5] config + wake words"
if [ ! -f "$DIR/config/config.yaml" ]; then
  # `setup` writes the production template (provider chains, model paths, ack TTS) and
  # generates keywords.txt from the default wake word, so the only things left by hand
  # are devices[] and the keys in .env. It still writes no devices: a fresh install must
  # not show a speaker that does not exist.
  docker run --rm -e RUI_INSTALLER=1 --user "$RUN_AS" -v "$DIR/config:/config" "$IMAGE" setup --config /config/config.yaml
  echo "      next: open http://<nas-lan-ip>:8080/ and finish the first-run wizard (/setup)"
  echo "            keys -> models -> prepare -> params -> wake -> connect -> test -> verify"
  echo "      no YAML editing is needed: the wizard writes devices[]; the device token is in .env"
  echo "      after changing wake words: docker run --rm --user $RUN_AS -v $DIR/config:/config $IMAGE keywords --config /config/config.yaml"
  echo "      reference: $DIR/config.example.yaml and docs/deploy.md"
else
  echo "      keeping existing config.yaml"
fi

echo "[3/5] env"
if [ ! -f "$DIR/.env" ]; then
  # One random secret per install, never printed except as a length.
  DEVICE_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  # The template is a quoted heredoc: backticks, $ and braces in these comments are
  # literal text. Without the quotes the comment below executes `docker compose up -d`
  # in the caller's working directory during install (dot2 #13) and the written .env no
  # longer matches the template. Values are printed after it, so nothing here expands.
  {
    cat <<'ENV'
RUI_LLM_KEY=
RUI_ASR_KEY=
VOLC_API_KEY=
XFYUN_APPID=
XFYUN_API_KEY=
XFYUN_API_SECRET=
RUI_HA_TOKEN=
# Values below are filled in by this installer for this install.
# RUI_IMAGE pins the image compose uses, so a later plain `docker compose up -d`
# (or a reboot) keeps the installed version instead of falling back to :latest.
# RUI_UID / RUI_GID are the container identity: compose reads them for `user:`, and
# the same pair owns config/data.
ENV
    printf 'RUI_DEVICE_TOKEN=%s\n' "$DEVICE_TOKEN"
    printf 'RUI_IMAGE=%s\n' "$IMAGE"
    printf 'RUI_UID=%s\n' "$RUI_UID"
    printf 'RUI_GID=%s\n' "$RUI_GID"
  } > "$DIR/.env"
  chmod 600 "$DIR/.env"
  echo "      wrote .env with a generated device token (${#DEVICE_TOKEN} hex chars)"
else
  echo "      keeping existing .env"
  # Upgrade hint (dot2 #12 C2): say which image is actually running and that an
  # upgrade is an explicit edit, never a silent rewrite of the pinned tag.
  CURRENT_IMAGE="$(sed -n 's/^RUI_IMAGE=//p' "$DIR/.env" | tail -n 1)"
  echo "      current RUI_IMAGE=${CURRENT_IMAGE:-<unset>}"
  echo "      upgrade: this installer never rewrites an existing image tag. Edit RUI_IMAGE"
  echo "               in $DIR/.env explicitly, then: docker compose pull && docker compose up -d"
  # Existing installs upgrading to the non-root image need the compose mapping persisted,
  # otherwise a later bare `docker compose up -d` falls back to 1000:1000 while the host
  # directories may be owned by another pair. Upsert only these two keys; every other
  # line (keys, tokens, image tag) is preserved byte for byte.
  if ! grep -qx "RUI_UID=$RUI_UID" "$DIR/.env" || ! grep -qx "RUI_GID=$RUI_GID" "$DIR/.env"; then
    TMP_ENV="$DIR/.env.tmp.$$"
    if awk -v uid="$RUI_UID" -v gid="$RUI_GID" '
        BEGIN { seen_uid = 0; seen_gid = 0 }
        /^RUI_UID=/ { print "RUI_UID=" uid; seen_uid = 1; next }
        /^RUI_GID=/ { print "RUI_GID=" gid; seen_gid = 1; next }
        { print }
        END {
          if (!seen_uid) print "RUI_UID=" uid
          if (!seen_gid) print "RUI_GID=" gid
        }
      ' "$DIR/.env" > "$TMP_ENV"; then
      chmod 600 "$TMP_ENV"
      mv "$TMP_ENV" "$DIR/.env"
      echo "      updated RUI_UID/RUI_GID in .env for the non-root container"
    else
      rm -f "$TMP_ENV"
      echo "ERROR: could not update RUI_UID/RUI_GID in $DIR/.env." >&2
      echo "       add these lines by hand and re-run:" >&2
      echo "         RUI_UID=$RUI_UID" >&2
      echo "         RUI_GID=$RUI_GID" >&2
      exit 1
    fi
  fi
fi

echo "[4/5] local models: ASR + VAD + wake word"
# Fresh images ship Silero VAD and the KWS wake model at /opt/seed-models. fetch-models
# copies those two out before its first network request, so a cold install only downloads
# SenseVoice (239 MB, hf-mirror) - the 33 MB GitHub release asset that used to stall is
# already in the image. An older image without /opt/seed-models falls back to the previous
# download path automatically; no flag needed here. Without ASR the chain silently falls
# back to cloud; without VAD the gate never starts; without KWS the box cannot hear its
# own wake word. A rerun must repair a *partial* install too, so run fetch-models
# whenever any required artifact is missing - not only when ASR and KWS happen to be
# present. fetch-models copies the image seed first, so this keeps the seed-first
# behavior and only downloads what is actually absent. A failure now makes the install
# exit non-zero (dot2 #14): the old warning-then-exit-0 let a transient 429 look like a
# successful install with no local ASR. RUI_SKIP_MODELS=1 is the explicit opt-out for a
# cloud-only/offline install; it is documented in docs/deploy.md.
ASR_DIR="$DIR/data/models/sensevoice"
VAD_FILE="$DIR/data/models/silero_vad.onnx"
KWS_DIR="$DIR/data/models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
KWS_TOKENS="$KWS_DIR/tokens.txt"
MISSING_MODELS=""
FETCH_FAILED=0
if [ ! -f "$ASR_DIR/model.int8.onnx" ] && [ ! -f "$ASR_DIR/model.onnx" ]; then
  MISSING_MODELS="$MISSING_MODELS asr-model"
fi
[ -f "$ASR_DIR/tokens.txt" ] || MISSING_MODELS="$MISSING_MODELS asr-tokens"
[ -f "$VAD_FILE" ] || MISSING_MODELS="$MISSING_MODELS vad"
[ -d "$KWS_DIR" ] || MISSING_MODELS="$MISSING_MODELS kws-dir"
[ -f "$KWS_TOKENS" ] || MISSING_MODELS="$MISSING_MODELS kws-tokens"
if [ "${RUI_SKIP_MODELS:-}" = "1" ]; then
  echo "      RUI_SKIP_MODELS=1: skipping the model check"
  echo "      ASR falls back to the cloud, the VAD gate never starts, the wake word stays off"
elif [ -z "$MISSING_MODELS" ]; then
  echo "      models already present (ASR + VAD + KWS)"
else
  echo "      missing:$MISSING_MODELS; running fetch-models (image seed first)"
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
  if docker run --rm --network host --user "$RUN_AS" -v "$DIR/data:/data" $FETCH_PROXY "$IMAGE" \
      fetch-models --dest /data/models/sensevoice \
      --vad-dest /data/models/silero_vad.onnx \
      --kws-dest /data/models/kws; then
    echo "      models ready (ASR + VAD + KWS)"
  else
    FETCH_FAILED=1
    echo "ERROR: model fetch failed; the install is not complete." >&2
    echo "       without ASR/VAD/KWS, ASR falls back to the cloud and the wake word stays off." >&2
  fi
fi
# setup already generated config/keywords.txt from the default wake word; only a change
# of devices[].wake_words needs a regeneration.
if [ ! -f "$DIR/config/keywords.txt" ]; then
  echo "      NOTE: config/keywords.txt missing (old install?): run the keywords command from docs/deploy.md"
fi

echo "[5/5] starting"
# Re-runnable by design. A container left by an earlier run of *this* installer is not a
# conflict: compare the compose project's recorded working directory instead of failing
# on the name alone, then let `docker compose up -d` reconcile/recreate it.
# A container owned by another directory is a real conflict, and 0.1.x used a different
# name. Never stop or remove containers here - print the exact command and let the owner
# decide (removing someone's production install is not a repair).
if docker ps -a --format '{{.Names}}' | grep -qx house-ear; then
  echo "ERROR: the old 'house-ear' container (0.1.x install) is still present." >&2
  echo "       0.2.x uses the name 'rui' and will not take over the old install." >&2
  echo "       stop the old install first (config/data on disk are not touched):" >&2
  echo "         docker stop house-ear && docker rm house-ear" >&2
  echo "       or, from the old install directory: docker compose down" >&2
  echo "       upgrade notes: docs/migration-house-ear-to-rui.md" >&2
  exit 1
fi
if docker ps -a --format '{{.Names}}' | grep -qx rui; then
  RUI_WORKDIR="$(docker inspect -f '{{with index .Config.Labels "com.docker.compose.project.working_dir"}}{{.}}{{end}}' rui 2>/dev/null || true)"
  RUI_PROJECT="$(docker inspect -f '{{with index .Config.Labels "com.docker.compose.project"}}{{.}}{{end}}' rui 2>/dev/null || true)"
  DIR_REAL="$(cd "$DIR" && pwd -P)"
  # Accept both spellings: compose records the path it was given, which may or may not
  # have gone through a symlink. A missing label means the container was not created by
  # compose, so this script cannot prove ownership and must not adopt it.
  if [ -z "$RUI_WORKDIR" ]; then
    echo "ERROR: a container named 'rui' exists but was not created by docker compose." >&2
    echo "       this installer will not adopt or remove it. stop/remove it manually:" >&2
    echo "         docker stop rui && docker rm rui" >&2
    echo "       then re-run this script. nothing was stopped or removed." >&2
    exit 1
  elif [ "$RUI_WORKDIR" = "$DIR" ] || [ "$RUI_WORKDIR" = "$DIR_REAL" ]; then
    echo "      reusing the existing 'rui' container from this install ($RUI_WORKDIR)"
  else
    echo "ERROR: container 'rui' belongs to another install (compose project '${RUI_PROJECT:-unknown}')." >&2
    echo "       its project directory:  $RUI_WORKDIR" >&2
    echo "       this install directory: $DIR" >&2
    echo "       stop that install from its own directory first:" >&2
    echo "         cd $RUI_WORKDIR && docker compose down" >&2
    echo "       nothing was stopped or removed." >&2
    exit 1
  fi
fi
cd "$DIR"
# --network host publishes no port mapping, so the compose label above is the owner
# signal. If the port is still taken (an unrelated service or a manually run container),
# say so now instead of letting uvicorn fail during startup. Skip the check when our own
# container is already running: it is expected to hold the port, and `up -d` reconciles it.
PORT="$(grep -E '^[[:space:]]*port:' "$DIR/config/config.yaml" 2>/dev/null | head -1 | awk '{print $2}')"
PORT="${PORT:-8080}"
if ! docker ps --format '{{.Names}}' | grep -qx rui; then
  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE "[:.]$PORT[[:space:]]"; then
    OWNER="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | awk -v p=":$PORT->" 'index($0, p) {print $1; exit}')"
    echo "WARNING: port $PORT is already in use${OWNER:+ by container '$OWNER'}."
    ss -ltnp 2>/dev/null | grep -E "[:.]$PORT[[:space:]]" | sed 's/^/         /' || true
    echo "         free it, or change web.port in $DIR/config/config.yaml, before starting."
  fi
fi
# Pass the compose file explicitly: it also pins the project directory, so a stray cwd
# can never make compose start the *other* install's project.
# Shell values win over .env, so an explicit RUI_UID/RUI_GID override on this run matches
# the chown above even if .env still holds an older pair (which the block above rewrote).
export RUI_UID RUI_GID
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
  echo "ERROR: service is still not healthy after 120 s." >&2
  echo "       probe: docker compose exec -T rui rui-healthcheck" >&2
  echo "       logs:  docker compose logs --tail=100 rui" >&2
  echo "       install did not complete; fix the failure above and re-run this script." >&2
  exit 1
fi

# Persist the console token where the operator can find it later (issue #4): setup
# generates it at "2/5" and never prints it (dot2 #13), so without this file the only
# recovery was to remember `docker compose exec`. Ask the running container for the same value the
# console expects (the `rui token` CLI; docs/deploy.md §2.4) and write it 0600.
# The token is captured, never echoed again. Idempotent: an existing file is kept
# byte for byte, so a rerun/upgrade never replaces a secret the operator may have
# already stored elsewhere. If the file disagrees with the running token (a rotate
# since the last run), say so and how to refresh it instead of silently overwriting.
CONSOLE_TOKEN_FILE="$DIR/console-token.txt"
CURRENT_TOKEN=""
# Command substitution already drops the trailing newline; the token is one line.
if ! CURRENT_TOKEN="$(docker compose exec -T rui rui token 2>/dev/null)"; then
  # On failure the CLI still prints an explanation to stdout; never treat that as the
  # token. Only a zero exit status is a value the console will accept.
  CURRENT_TOKEN=""
fi
if [ -z "$CURRENT_TOKEN" ]; then
  echo "      WARNING: could not read the console token from the container."
  echo "               recover it with: cd $DIR && docker compose exec -T rui rui token"
elif [ -f "$CONSOLE_TOKEN_FILE" ]; then
  if [ "$(cat "$CONSOLE_TOKEN_FILE" 2>/dev/null)" = "$CURRENT_TOKEN" ]; then
    echo "      console token already saved: $CONSOLE_TOKEN_FILE"
  else
    echo "      WARNING: $CONSOLE_TOKEN_FILE does not match the running console token."
    echo "               if you rotated it, refresh the file:"
    echo "                 cd $DIR && docker compose exec -T rui rui token > console-token.txt"
    echo "                 chmod 600 console-token.txt"
  fi
else
  TMP_TOKEN="$CONSOLE_TOKEN_FILE.tmp.$$"
  # One subshell: umask keeps the transient file private, and a failure (read-only
  # RUI_HOME, full disk) stays a warning instead of aborting under `set -e`.
  if ( umask 077 \
      && printf '%s\n' "$CURRENT_TOKEN" > "$TMP_TOKEN" \
      && chmod 600 "$TMP_TOKEN" \
      && mv "$TMP_TOKEN" "$CONSOLE_TOKEN_FILE" ) 2>/dev/null; then
    echo "      console token saved: $CONSOLE_TOKEN_FILE (mode 600)"
  else
    rm -f "$TMP_TOKEN" 2>/dev/null || true
    echo "      WARNING: could not write $CONSOLE_TOKEN_FILE; the install is still usable."
    echo "               print the token with: cd $DIR && docker compose exec -T rui rui token"
  fi
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "done. open http://${IP:-127.0.0.1}:${PORT}/"
if [ -f "$CONSOLE_TOKEN_FILE" ]; then
  echo "  console: token saved to $CONSOLE_TOKEN_FILE (mode 600) - the 401 dialog points here"
else
  echo "  console: token -> cd $DIR && docker compose exec -T rui rui token"
fi
echo "  doctor:  cd $DIR && docker compose exec rui rui doctor"
echo "  logs:    cd $DIR && docker compose logs -f rui"
echo "  keys:    open the console -> Setup -> Keys (one provider per use), or fill $DIR/.env"
echo "  speaker: open the console -> Setup -> Connect to copy the ws URL + device token"
echo "           (step-by-step: docs/open-xiaoai-client.md)"
echo "  pin:     image is pinned in $DIR/.env ($IMAGE); edit that line to change version"
echo "  rollback: set the previous tag in $DIR/.env and run docker compose up -d"
echo "  prune:   old images are kept on purpose for rollback; once done: docker image prune -f"
echo "  re-run:  this script is safe to run again; it reconciles this directory's container"

# A model fetch failure is a failed install (dot2 #14). The service is already up so the
# wizard stays reachable, but the exit status must not hide the missing models: callers
# (CI, a NAS job, the operator's shell) read 0 as "everything is ready". The opt-out is
# RUI_SKIP_MODELS=1 in [4/5].
if [ "$FETCH_FAILED" = "1" ]; then
  echo
  echo "ERROR: install finished with ASR/VAD/KWS models missing." >&2
  echo "       the service is running (the wizard still works), but local models are not ready:" >&2
  echo "       re-run the download, then restart:" >&2
  echo "         docker run --rm --user $RUN_AS -v \"$DIR/data:/data\" \"$IMAGE\" fetch-models \\" >&2
  echo "           --dest /data/models/sensevoice --vad-dest /data/models/silero_vad.onnx --kws-dest /data/models/kws" >&2
  echo "         cd $DIR && docker compose up -d" >&2
  echo "       or re-run this installer (same directory is safe); it repairs a partial tree." >&2
  echo "       cloud-only install: re-run with RUI_SKIP_MODELS=1 to accept the degradation." >&2
  exit 1
fi
