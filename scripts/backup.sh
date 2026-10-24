#!/usr/bin/env bash
# Back up the files that make a rui install recoverable: config/ and data/
# (models excluded by default - they are a re-download), plus .env and compose.
#
# Safety rules:
#   * archive is 0600 and lives in an explicit --out directory;
#   * secret VALUES are never printed (only "included" / "excluded");
#   * every run writes a manifest (versions + contents) and a SHA-256 sidecar;
#   * nothing outside --home is read.
#
# Usage:
#   scripts/backup.sh [--home DIR] [--out DIR] [--with-models] [--no-env] [--dry-run]
#
# Exit codes: 0 ok, 2 bad arguments, 3 missing/unreadable source, 4 archive failed.

set -euo pipefail
umask 077

FORMAT=1
SCRIPT_VERSION="1"
die() { printf 'backup: %s\n' "$1" >&2; exit "${2:-2}"; }

home="${RUI_HOME:-${HOUSE_EAR_HOME:-$HOME/rui}}"
out="${RUI_BACKUP_DIR:-${HOUSE_EAR_BACKUP_DIR:-$HOME/rui-backups}}"
with_models=0
with_env=1
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --home)       [ "$#" -ge 2 ] || die "--home needs a value"; home="$2"; shift 2 ;;
    --out)        [ "$#" -ge 2 ] || die "--out needs a value"; out="$2"; shift 2 ;;
    --with-models) with_models=1; shift ;;
    --no-env)     with_env=0; shift ;;
    --dry-run)    dry_run=1; shift ;;
    -h|--help)    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            die "unknown argument: $1" ;;
  esac
done

# Resolve and validate the source. Never back up "/" or a directory that is not an
# install: a wrong --home must fail loudly, not tar the wrong tree.
[ -d "$home" ] || die "home directory does not exist: $home" 3
home="$(cd "$home" && pwd -P)"
[ "$home" != "/" ] || die "refusing to back up /" 2
[ -d "$home/config" ] || die "no config/ in $home (not a rui install?)" 3
[ -d "$home/data" ] || die "no data/ in $home" 3

out="${out/#\~/$HOME}"
mkdir -p "$out"
out="$(cd "$out" && pwd -P)"
[ "$out" != "/" ] || die "refusing to write the archive to /" 2
chmod 700 "$out"

stamp="$(date +%Y%m%d-%H%M%S)"
archive="$out/rui-backup-$stamp.tar.gz"
stage="$(mktemp -d "${TMPDIR:-/tmp}/rui-backup.XXXXXX")"
cleanup() {
  rm -rf -- "$stage"
  if [ "${keep_archive:-0}" != 1 ] && [ -f "$archive" ] && [ "$dry_run" = 0 ]; then
    # Only reached on failure: a half-written archive must not look restorable.
    rm -f -- "$archive" "$archive.sha256" "$archive.manifest.json"
  fi
}
keep_archive=0
trap cleanup EXIT

# ---------------------------------------------------------------- versions
config_version=""
if [ -f "$home/config/config.yaml" ]; then
  config_version="$(awk '/^version:[[:space:]]*/{print $2; exit}' "$home/config/config.yaml" 2>/dev/null || true)"
fi
image=""
image_id=""
image_version=""
if command -v docker >/dev/null 2>&1; then
  image="$(docker inspect rui --format '{{.Config.Image}}' 2>/dev/null || true)"
  image_id="$(docker inspect rui --format '{{.Image}}' 2>/dev/null || true)"
  image_version="$(docker inspect rui --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null || true)"
fi
case "$image_version" in '<no value>'|'') image_version="unknown" ;; esac
[ -n "$image" ] || image="unknown"
[ -n "$image_id" ] || image_id="unknown"
[ -n "$config_version" ] || config_version="unknown"
host_name="$(hostname 2>/dev/null || echo unknown)"

# ---------------------------------------------------------------- what goes in
files_total=0
bytes_total=0
if [ "$dry_run" = 1 ]; then
  printf 'backup (dry run)\n  home:    %s\n  out:     %s\n' "$home" "$out"
  printf '  include: config/ data/%s%s\n' \
    "$([ "$with_models" = 1 ] && printf '.' || printf ' (excluding data/models)')" \
    "$([ "$with_env" = 1 ] && printf ' .env (0600)' || printf ' (no .env)')"
  du -sh "$home/config" "$home/data" 2>/dev/null || true
  exit 0
fi

mkdir -p "$stage/config" "$stage/data"
cp -a "$home/config/." "$stage/config/"
cp -a "$home/data/." "$stage/data/"
if [ "$with_models" = 0 ]; then
  rm -rf -- "$stage/data/models"
fi
env_included=false
if [ "$with_env" = 1 ] && [ -f "$home/.env" ]; then
  cp -a "$home/.env" "$stage/.env"
  env_included=true
fi
compose_included=false
if [ -f "$home/docker-compose.yml" ]; then
  cp -a "$home/docker-compose.yml" "$stage/docker-compose.yml"
  compose_included=true
fi

files_total="$(find "$stage" -type f 2>/dev/null | wc -l | tr -d ' ')"
bytes_total="$(du -sk "$stage" 2>/dev/null | awk '{print $1 * 1024}')"

# ---------------------------------------------------------------- manifest
cat > "$stage/MANIFEST.json" <<JSON
{
  "backup_format": $FORMAT,
  "tool": "rui backup.sh",
  "tool_version": "$SCRIPT_VERSION",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "host": "$host_name",
  "home": "$home",
  "config_version": "$config_version",
  "image": "$image",
  "image_id": "$image_id",
  "image_version": "$image_version",
  "contents": {
    "config": true,
    "data": true,
    "models": $([ "$with_models" = 1 ] && echo true || echo false),
    "env": $env_included,
    "docker_compose": $compose_included
  },
  "excluded": $([ "$with_models" = 1 ] && echo '[]' || echo '["data/models"]'),
  "files": $files_total
}
JSON

chmod 600 "$stage/MANIFEST.json"
tar -czf "$archive" -C "$stage" .
chmod 600 "$archive"

sha=""
if command -v sha256sum >/dev/null 2>&1; then
  sha="$(sha256sum "$archive" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  sha="$(shasum -a 256 "$archive" | awk '{print $1}')"
else
  echo '{"error":"no sha256 tool found (sha256sum/shasum)"}' > "$archive.sha256"
  chmod 600 "$archive.sha256"
  die "cannot compute SHA-256 on this host" 4
fi
printf '%s  %s\n' "$sha" "$(basename "$archive")" > "$archive.sha256"
cp -a "$stage/MANIFEST.json" "$archive.manifest.json"
chmod 600 "$archive.sha256" "$archive.manifest.json"
keep_archive=1

printf 'backup ok\n'
printf '  archive:  %s\n' "$archive"
printf '  size:     %s KiB (%s files)\n' "$((bytes_total / 1024))" "$files_total"
printf '  sha256:   %s\n' "$sha"
printf '  version:  config=%s image=%s (%s)\n' "$config_version" "$image" "$image_version"
printf '  contents: config/, data/ %s%s%s\n' \
  "$([ "$with_models" = 1 ] && printf '(models included)' || printf '(models excluded)')" \
  "$([ "$env_included" = true ] && printf ', .env (0600, values not printed)' || printf '')" \
  "$([ "$compose_included" = true ] && printf ', docker-compose.yml' || printf '')"
printf '  manifest: %s\n' "$archive.manifest.json"
if [ "$env_included" = true ]; then
  printf '  note:     keep this archive private; it contains config, history and keys (.env).\n'
else
  printf '  note:     archive contains config/history but no .env; keep it private anyway.\n'
fi
