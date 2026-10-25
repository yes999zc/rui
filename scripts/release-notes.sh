#!/usr/bin/env bash
# Build release notes from Conventional Commits. Uses only git and POSIX tools:
# no network access, no Python/Node dependency, nothing is tagged or published.
#
# Sections are derived from the commit type:
#   Added          feat
#   Fixed          fix, revert
#   Changed        perf, refactor, build, ci, test, style, unknown types
#   Security       type "security" or scope "(security)"
#   Upgrade notes  commits with "!" or a "BREAKING CHANGE:" trailer
# docs/chore commits are skipped unless --all is given; "release:" commits are
# always skipped (they only bump the version).
#
# Usage:
#   scripts/release-notes.sh [--version X.Y.Z] [--from REF] [--to REF]
#                            [--out FILE] [--repo URL] [--all]
#
# Examples:
#   scripts/release-notes.sh --from v0.3.4 --to v0.4.0
#   scripts/release-notes.sh --version 0.4.0 --from v0.3.4 --to v0.4.0 \
#       --out docs/releases/v0.4.0.generated.md
#
# Defaults: --to HEAD; --from the previous tag; --version from the tag at --to.
# Exit codes: 0 ok, 2 bad arguments, 3 not a git repository / unknown ref.

set -euo pipefail

repo_url="https://github.com/yes999zc/rui"
version=""
from=""
to="HEAD"
out=""
include_all=0

die() { printf 'release-notes: %s\n' "$1" >&2; exit "${2:-2}"; }

usage() {
  cat <<'EOF'
release-notes.sh - build release notes from Conventional Commits

Usage:
  scripts/release-notes.sh [--version X.Y.Z] [--from REF] [--to REF]
                           [--out FILE] [--repo URL] [--all]

Options:
  --version X.Y.Z  heading version (default: tag at --to, else "Unreleased")
  --from REF       start ref (default: previous tag)
  --to REF         end ref (default: HEAD)
  --out FILE       write the draft to FILE instead of stdout (never publishes)
  --repo URL       repository URL used for commit/compare links
  --all            include docs/chore/test/ci/build commits in Changed

Sections: Added, Changed, Fixed, Security, Upgrade notes.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)   [ "$#" -ge 2 ] || die "--version needs a value"; version="$2"; shift 2 ;;
    --version=*) version=$(printf '%s' "$1" | cut -d= -f2-); shift ;;
    --from)      [ "$#" -ge 2 ] || die "--from needs a value"; from="$2"; shift 2 ;;
    --from=*)    from=$(printf '%s' "$1" | cut -d= -f2-); shift ;;
    --to)        [ "$#" -ge 2 ] || die "--to needs a value"; to="$2"; shift 2 ;;
    --to=*)      to=$(printf '%s' "$1" | cut -d= -f2-); shift ;;
    --out)       [ "$#" -ge 2 ] || die "--out needs a value"; out="$2"; shift 2 ;;
    --out=*)     out=$(printf '%s' "$1" | cut -d= -f2-); shift ;;
    --repo)      [ "$#" -ge 2 ] || die "--repo needs a value"; repo_url="$2"; shift 2 ;;
    --repo=*)    repo_url=$(printf '%s' "$1" | cut -d= -f2-); shift ;;
    --all)       include_all=1; shift ;;
    --help|-h)   usage; exit 0 ;;
    *)           die "unknown argument: $1" ;;
  esac
done

repo_url=$(printf '%s' "$repo_url" | sed 's:/*$::')

git rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository" 3
to_commit=$(git rev-parse -q --verify "${to}^{commit}") || die "unknown ref: $to" 3

if [ -n "$from" ]; then
  from_commit=$(git rev-parse -q --verify "${from}^{commit}") || die "unknown ref: $from" 3
else
  from=$(git describe --tags --abbrev=0 "${to_commit}^" 2>/dev/null || true)
  if [ -n "$from" ]; then
    from_commit=$(git rev-parse -q --verify "${from}^{commit}") || die "unknown ref: $from" 3
  else
    from_commit=""
  fi
fi

if [ -n "$from_commit" ]; then
  range="${from_commit}..${to_commit}"
else
  range="${to_commit}"
fi

if [ -z "$version" ]; then
  exact_tag=$(git describe --tags --exact-match "$to_commit" 2>/dev/null || true)
  if [ -n "$exact_tag" ]; then
    version=$(printf '%s' "$exact_tag" | sed 's/^v//')
  else
    version="Unreleased"
  fi
fi

date_str=$(git log -1 --format=%ad --date=short "$to_commit")
tmpbase=$(mktemp -d "${TMPDIR:-/tmp}/rui-release-notes.XXXXXX" 2>/dev/null) \
  || tmpbase=$(mktemp -d -t rui-release-notes 2>/dev/null) \
  || die "cannot create a temporary directory" 3
tmp="$tmpbase"
trap 'rm -rf "$tmp"' EXIT INT TERM
: > "$tmp/added"; : > "$tmp/changed"; : > "$tmp/fixed"
: > "$tmp/security"; : > "$tmp/breaking"

short_hash() { printf '%s' "$1" | cut -c1-7; }

# Add one bullet to a section file. Usage: add_bullet FILE SCOPE DESCRIPTION HASH
add_bullet() {
  local file="$1" scope="$2" desc="$3" hash="$4" short
  short=$(short_hash "$hash")
  if [ -n "$scope" ]; then
    printf -- '- **%s:** %s ([%s](%s/commit/%s))\n' \
      "$scope" "$desc" "$short" "$repo_url" "$hash" >> "$tmp/$file"
  else
    printf -- '- %s ([%s](%s/commit/%s))\n' \
      "$desc" "$short" "$repo_url" "$hash" >> "$tmp/$file"
  fi
}

subject_re='^([A-Za-z]+)(\(([^()]*)\))?(!)?:[[:space:]]*(.*)$'

while IFS= read -r -d '' hash \
   && IFS= read -r -d '' subject \
   && IFS= read -r -d '' body; do
  if [[ "$subject" =~ $subject_re ]]; then
    ctype=$(printf '%s' "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')
    scope="${BASH_REMATCH[3]}"
    bang="${BASH_REMATCH[4]}"
    desc="${BASH_REMATCH[5]}"
  else
    ctype=""; scope=""; bang=""; desc="$subject"
  fi
  [ -n "$desc" ] || desc="$subject"

  breaking=0
  if [ "$bang" = "!" ]; then
    breaking=1
  fi
  if printf '%s' "$body" | grep -qE '^BREAKING([ -])CHANGE:'; then
    breaking=1
  fi

  # Release commits only bump the version; never list them.
  if [ "$ctype" = "release" ]; then
    continue
  fi

  case "$ctype" in
    feat)          dest=added ;;
    fix|revert)    dest=fixed ;;
    security)      dest=security ;;
    perf|refactor) dest=changed ;;
    docs|chore|test|ci|build|style)
      if [ "$include_all" -ne 1 ]; then
        continue
      fi
      dest=changed ;;
    *)             dest=changed ;;
  esac

  if [ "$ctype" != "security" ] && [ "$scope" = "security" ]; then
    dest=security
  fi

  add_bullet "$dest" "$scope" "$desc" "$hash"

  if [ "$breaking" -eq 1 ]; then
    add_bullet breaking "$scope" "$desc" "$hash"
  fi
done < <(git log -z --no-merges --format='%H%x00%s%x00%b' "$range")

emit_section() {
  local title="$1" file="$2"
  [ -s "$tmp/$file" ] || return 0
  printf '\n### %s\n\n' "$title"
  cat "$tmp/$file"
}

{
  printf '## %s - %s\n' "$version" "$date_str"
  emit_section "Added" added
  emit_section "Changed" changed
  emit_section "Fixed" fixed
  emit_section "Security" security
  emit_section "Upgrade notes" breaking
  bullet_count=$(cat "$tmp/added" "$tmp/changed" "$tmp/fixed" "$tmp/security" 2>/dev/null | wc -l | tr -d ' ')
  if [ "${bullet_count:-0}" -eq 0 ]; then
    printf '\n_No user-facing changes in this range._\n'
  fi
  if [ -n "$from_commit" ] && [ -n "$from" ]; then
    printf '\n**Full changelog:** [%s...%s](%s/compare/%s...%s)\n' \
      "$from" "$to" "$repo_url" "$from" "$to"
  fi
} > "$tmp/notes.md"

if [ -n "$out" ]; then
  mkdir -p "$(dirname "$out")"
  {
    printf '<!-- Generated by scripts/release-notes.sh from %s to %s on %s. -->\n' \
      "${from:-initial}" "$to" "$date_str"
    printf '<!-- Machine draft: curate it into docs/releases/v%s.md before publishing. -->\n\n' "$version"
    cat "$tmp/notes.md"
  } > "$out"
  printf 'release-notes: wrote %s (not published)\n' "$out" >&2
else
  cat "$tmp/notes.md"
fi
