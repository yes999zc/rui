#!/usr/bin/env bash
# Generate a self-signed TLS pair for the dot2 link (development / first install).
#
#   bash scripts/make_dev_tls.sh [output_dir] [--force]
#
# Default output_dir is ./secrets, which is what examples/two-speakers/compose.yaml
# mounts at /opt/rui-secrets. Run it from the directory that holds ./secrets, e.g.
#
#   cd examples/two-speakers
#   bash ../../scripts/make_dev_tls.sh
#
# Self-signed only proves that the audio plane can start. Whether the real device
# accepts it depends on the CA the device trusts; the original controller certificate
# cannot be recreated. See docs/audit/tls-required-2026-10.md.
set -euo pipefail

force=0
out="./secrets"
for arg in "$@"; do
  case "$arg" in
    --force) force=1 ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
    *) out="$arg" ;;
  esac
done

cert="$out/server.pem"
key="$out/server.key"
if [[ -e "$cert" || -e "$key" ]]; then
  if [[ "$force" -ne 1 ]]; then
    echo "refusing to overwrite existing TLS material in $out (use --force)" >&2
    exit 1
  fi
  rm -f "$cert" "$key"
fi

mkdir -p "$out"
chmod 700 "$out"
umask 077
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 825 \
  -keyout "$key" -out "$cert" \
  -subj "/CN=echomuse-controller" \
  -addext "subjectAltName=DNS:echomuse-controller" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" >/dev/null 2>&1
chmod 600 "$key"
chmod 644 "$cert"

echo "wrote $cert and $key"
openssl x509 -in "$cert" -noout -subject -dates -ext subjectAltName
