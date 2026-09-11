#!/usr/bin/env bash
# hermes-router install/copy helper for a single Hermes profile.
# Usage: bash scripts/install.sh <profile-name> [plugins-root]
#   profile-name   e.g. researcher
#   plugins-root   defaults to /opt/data/.hermes/profiles/<profile>/plugins
set -euo pipefail

PROFILE="${1:?usage: install.sh <profile-name> [plugins-root]}"
ROOT="${2:-/opt/data/.hermes/profiles/${PROFILE}/plugins}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DST="${ROOT}/hermes_router"

if [[ ! -d "${ROOT}" ]]; then
  echo "plugins root ${ROOT} does not exist — create it or pass the right root" >&2
  exit 1
fi

mkdir -p "${DST}"
rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
      --exclude 'benchmarks' --exclude 'tests' "${SRC}/" "${DST}/"

echo "deployed ${SRC} -> ${DST}"
echo "next: /command/s6-svc -r /run/service/gateway-${PROFILE}"
echo "verify: /command/s6-svstat /run/service/gateway-${PROFILE}  (then /router in chat)"
