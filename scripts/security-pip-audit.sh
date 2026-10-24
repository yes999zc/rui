#!/usr/bin/env bash
# Audit Rui's locked Python dependencies (all extras) with pip-audit.
#
# The environment is intentionally not installed: `uv export` reads uv.lock and
# emits the fully pinned set, so the audit covers exactly what a `uv sync
# --all-extras` would install without paying for native builds (sherpa-onnx,
# pilk). Accepted findings live in security/pip-audit-ignore.txt and must carry
# an expiry date and a justification; an expired entry fails here.
#
# Usage:
#   ./scripts/security-pip-audit.sh
#   SECURITY_REPORT_DIR=/tmp/reports PIP_AUDIT_VERSION=2.10.1 ./scripts/security-pip-audit.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REPORT_DIR="${SECURITY_REPORT_DIR:-$ROOT/security-reports}"
IGNORE_FILE="${PIP_AUDIT_IGNORE_FILE:-$ROOT/security/pip-audit-ignore.txt}"
PIP_AUDIT_VERSION="${PIP_AUDIT_VERSION:-2.10.1}"

UV_BIN="${UV_BIN:-uv}"
if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    if [[ -x "$HOME/.local/bin/uv" ]]; then
        UV_BIN="$HOME/.local/bin/uv"
    else
        echo "error: uv not found; install it from https://docs.astral.sh/uv/" >&2
        exit 2
    fi
fi

mkdir -p "$REPORT_DIR"
REQ_FILE="$(mktemp "${TMPDIR:-/tmp}/rui-pip-audit-req.XXXXXX")"
AUDIT_CACHE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rui-pip-audit-cache.XXXXXX")"
trap 'rm -f "$REQ_FILE"; rm -rf "$AUDIT_CACHE_DIR"' EXIT

echo "==> exporting locked dependencies (all extras) from uv.lock"
"$UV_BIN" export \
    --frozen \
    --all-extras \
    --no-hashes \
    --no-emit-project \
    --format requirements.txt \
    --output-file "$REQ_FILE" >/dev/null

if [[ ! -f "$IGNORE_FILE" ]]; then
    echo "error: missing ignore file: $IGNORE_FILE" >&2
    exit 2
fi

ignore_args=()
ignore_count=0
today="$(date -u +%F)"
while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue

    read -r id expires justification <<< "$line"
    if [[ -z "${id:-}" || -z "${expires:-}" || -z "${justification:-}" ]]; then
        echo "error: $IGNORE_FILE: expected '<ID> <expires YYYY-MM-DD> <justification>': $line" >&2
        exit 2
    fi
    if [[ ! "$id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
        echo "error: $IGNORE_FILE: invalid vulnerability id: $id" >&2
        exit 2
    fi
    if [[ ! "$expires" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        echo "error: $IGNORE_FILE: $id expiry '$expires' is not YYYY-MM-DD" >&2
        exit 2
    fi
    if [[ "$expires" < "$today" ]]; then
        echo "error: $IGNORE_FILE: ignore for $id expired on $expires (today: $today)" >&2
        echo "       Fix the dependency or renew the entry with a fresh justification." >&2
        exit 2
    fi
    ignore_args+=(--ignore-vuln "$id")
    ignore_count=$((ignore_count + 1))
done < "$IGNORE_FILE"

echo "==> accepted (unexpired) pip-audit ignores: ${ignore_count}"
if [[ ${ignore_count} -gt 0 ]]; then
    echo "::warning title=pip-audit allowlist::pip-audit ignored ${ignore_count} dated vulnerabilities; review security/pip-audit-ignore.txt before expiry."
fi
echo "==> pip-audit $PIP_AUDIT_VERSION (locked requirements, no install)"
rm -f "$REPORT_DIR/pip-audit.json" "$REPORT_DIR/pip-audit.txt"

set +e
"$UV_BIN" tool run --from "pip-audit==$PIP_AUDIT_VERSION" pip-audit \
    --requirement "$REQ_FILE" \
    --no-deps \
    --disable-pip \
    --strict \
    --progress-spinner off \
    --cache-dir "$AUDIT_CACHE_DIR" \
    --format json \
    --output "$REPORT_DIR/pip-audit.json" \
    ${ignore_args[@]+"${ignore_args[@]}"}
audit_rc=$?
set -e

if [[ ! -s "$REPORT_DIR/pip-audit.json" ]]; then
    echo "error: pip-audit did not produce $REPORT_DIR/pip-audit.json (exit $audit_rc)" >&2
    exit 2
fi

python3 - "$REPORT_DIR/pip-audit.json" "$REPORT_DIR/pip-audit.txt" <<'PY'
import json
import sys

source, destination = sys.argv[1], sys.argv[2]
with open(source, encoding="utf-8") as handle:
    data = json.load(handle)

dependencies = data.get("dependencies") or []
findings = []
for dependency in dependencies:
    for vulnerability in dependency.get("vulns") or []:
        fixes = ", ".join(vulnerability.get("fix_versions") or []) or "none"
        findings.append(
            (dependency["name"], dependency["version"], vulnerability["id"], fixes)
        )

lines = [
    f"pip-audit: {len(dependencies)} packages, {len(findings)} known vulnerabilities",
]
for name, version, vuln_id, fixes in findings:
    lines.append(f"  {name}=={version}  {vuln_id}  fix={fixes}")
with open(destination, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
print("\n".join(lines))
PY

if (( audit_rc != 0 )); then
    echo "error: pip-audit failed (exit $audit_rc); see $REPORT_DIR/pip-audit.txt" >&2
    exit "$audit_rc"
fi

echo "pip-audit: no known vulnerabilities"
