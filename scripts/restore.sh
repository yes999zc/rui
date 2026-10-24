#!/usr/bin/env bash
# Restore a rui backup made by scripts/backup.sh (or a compatible tar.gz).
#
# Safety rules:
#   * verifies <archive>.sha256 when present, and refuses path-unsafe archives;
#   * extracts to a staging directory first; the live tree is not touched until the
#     archive has passed its checks;
#   * refuses to overwrite a non-empty target unless --force;
#   * with --force, the previous config/data/.env are moved aside as
#     *.pre-restore-<timestamp> - never deleted - so a bad restore is reversible;
#   * does NOT start/stop containers. Restart is a separate, deliberate step.
#
# Usage:
#   scripts/restore.sh ARCHIVE [--home DIR] [--force] [--dry-run]
#
# Exit codes: 0 ok, 2 bad arguments, 3 unsafe/missing archive, 4 target not empty
# (no --force), 5 checksum/format failure.

set -euo pipefail
umask 077

FORMAT=1
die() { printf 'restore: %s\n' "$1" >&2; exit "${2:-2}"; }

archive=""
home="${RUI_HOME:-${HOUSE_EAR_HOME:-$HOME/rui}}"
force=0
dry_run=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --home)    [ "$#" -ge 2 ] || die "--home needs a value"; home="$2"; shift 2 ;;
    --force)   force=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)        die "unknown argument: $1" ;;
    *)         [ -z "$archive" ] || die "unexpected extra argument: $1"; archive="$1"; shift ;;
  esac
done
[ -n "$archive" ] || die "usage: restore.sh ARCHIVE [--home DIR] [--force] [--dry-run]"

archive="${archive/#\~/$HOME}"
[ -f "$archive" ] || die "archive not found: $archive" 3
archive="$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")"
home="${home/#\~/$HOME}"
mkdir -p "$home"
home="$(cd "$home" && pwd -P)"
[ "$home" != "/" ] || die "refusing to restore into /" 2

# ---------------------------------------------------------------- checksum
if [ -f "$archive.sha256" ]; then
  expected="$(awk '{print $1}' "$archive.sha256")"
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$archive" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
  else
    die "no sha256 tool on this host; cannot verify the archive" 5
  fi
  [ "$expected" = "$actual" ] || die "sha256 mismatch: archive is corrupt or was modified" 5
  printf 'checksum ok\n'
else
  printf 'warning: no %s.sha256 sidecar; cannot verify integrity\n' "$archive" >&2
fi

# Only relative, non-traversing paths: a restore must not write outside --home.
tar -tzf "$archive" >/dev/null || die "archive is not a readable tar.gz" 3
unsafe="$(tar -tzf "$archive" | awk '
  /^\// || /(^|\/)\.\.(\/|$)/ { print; next }
' | head -n 5)"
[ -z "$unsafe" ] || die "archive contains path-unsafe entries: $unsafe" 3

stage="$(mktemp -d "${TMPDIR:-/tmp}/rui-restore.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
tar -xzf "$archive" -C "$stage"

# Archives made by backup.sh have ./config and ./data. Older hand-rolled tars may
# contain config/ data/ without ./; accept both, but reject anything else.
if [ -d "$stage/config" ]; then
  src="$stage"
elif [ -d "$stage/rui/config" ]; then
  src="$stage/rui"
else
  die "archive has no config/ directory (not a rui backup)" 5
fi

manifest_format=""
if [ -f "$src/MANIFEST.json" ]; then
  manifest_format="$(awk -F':[[:space:]]*' '/"backup_format"/{gsub(/[^0-9]/,"",$2); print $2; exit}' "$src/MANIFEST.json" 2>/dev/null || true)"
  if [ -n "$manifest_format" ] && [ "$manifest_format" -gt "$FORMAT" ]; then
    die "backup format $manifest_format is newer than this restore script ($FORMAT)" 5
  fi
  config_version="$(awk -F'"' '/"config_version"/{print $4; exit}' "$src/MANIFEST.json" 2>/dev/null || true)"
  image_version="$(awk -F'"' '/"image_version"/{print $4; exit}' "$src/MANIFEST.json" 2>/dev/null || true)"
  printf 'backup: format=%s config_version=%s image_version=%s\n' \
    "${manifest_format:-?}" "${config_version:-?}" "${image_version:-?}"
fi

# ---------------------------------------------------------------- target state
nonempty=0
for name in config data; do
  if [ -d "$home/$name" ] && [ -n "$(ls -A "$home/$name" 2>/dev/null || true)" ]; then
    nonempty=1
  fi
done
[ -f "$home/.env" ] && nonempty=1
if [ "$nonempty" = 1 ] && [ "$force" = 0 ]; then
  die "target is not empty: $home/config or $home/data has files (use --force to replace; the old tree is moved aside, not deleted)" 4
fi

stamp="$(date +%Y%m%d-%H%M%S)"
plan=""
for name in config data .env docker-compose.yml; do
  [ -e "$src/$name" ] || continue
  if [ -e "$home/$name" ]; then
    plan="$plan $name->${name}.pre-restore-$stamp"
  else
    plan="$plan $name"
  fi
done
printf 'restore plan (home=%s):%s\n' "$home" "${plan:- (nothing)}"
if [ "$dry_run" = 1 ]; then
  printf 'dry run: nothing written\n'
  exit 0
fi
if [ -z "$plan" ]; then
  die "archive contains none of config/ data/ .env/ docker-compose.yml" 5
fi

place() {
  name="$1"
  [ -e "$src/$name" ] || return 0
  if [ -e "$home/$name" ]; then
    if [ -d "$home/$name" ] && [ -z "$(ls -A "$home/$name" 2>/dev/null || true)" ]; then
      rmdir -- "$home/$name"
    else
      mv -- "$home/$name" "$home/$name.pre-restore-$stamp"
    fi
  fi
  mv -- "$src/$name" "$home/$name"
}

place config
place data
place .env
place docker-compose.yml

# Config and data contain secrets (keys, tokens, transcripts, credentials). Keep them
# owner-only regardless of the modes that happened to be in the archive.
[ -d "$home/config" ] && chmod -R go-rwx "$home/config"
[ -d "$home/data" ] && chmod -R go-rwx "$home/data"
[ -f "$home/.env" ] && chmod 600 "$home/.env"

printf 'restore ok\n'
printf '  home: %s\n' "$home"
if [ -n "$manifest_format" ]; then
  printf '  backup format: %s\n' "$manifest_format"
fi
printf 'next steps (deliberate):\n'
printf '  1. cd %s\n' "$home"
cat <<'NEXT'
  2. docker compose up -d
  3. wait until: docker compose ps  -> healthy
  4. docker compose exec rui rui doctor
  5. check: curl -s http://127.0.0.1:8080/health | python3 -m json.tool
  6. if the restore was wrong: move *.pre-restore-<timestamp> back into place.
NEXT
