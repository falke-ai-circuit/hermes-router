#!/usr/bin/env bash
# hermes-router install — ONE route: copy this checkout into a Hermes home's
# plugins directory (the directory the loader scans: <hermes_home>/plugins/).
#
# No rsync (cp/tar only), no destructive mirror (nothing is ever deleted from
# the target), no fleet-literal path (target is a parameter), and safe to run
# twice (idempotent: same source -> same target tree).
#
# Usage:
#   bash scripts/install.sh [options]
#
#   --hermes-home DIR    Hermes home to install into.
#                        Default: $HERMES_HOME, else ~/.hermes
#   --plugins-root DIR   install into DIR/hermes_router (overrides --hermes-home)
#   --profile NAME       profile label for messages only.
#                        Default: $HERMES_PROFILE (never used to build a path)
#   --dry-run            print the plan, write nothing
#   -h, --help           this text
#
# Exit codes: 0 ok · 2 usage · 1 install failure
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DRY_RUN=0
HERMES_HOME_ARG=""
PLUGINS_ROOT=""
PROFILE="${HERMES_PROFILE:-}"

usage() {
  cat <<'USAGE'
usage: bash scripts/install.sh [options]

  --hermes-home DIR    Hermes home to install into.
                       Default: $HERMES_HOME, else ~/.hermes
  --plugins-root DIR   install into DIR/hermes_router (overrides --hermes-home)
  --profile NAME       profile label for messages only.
                       Default: $HERMES_PROFILE (never used to build a path)
  --dry-run            print the plan, write nothing
  -h, --help           this text

Installs the checkout into <hermes_home>/plugins/hermes_router by file copy.
Idempotent, never deletes, needs no rsync.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)       DRY_RUN=1; shift ;;
    --hermes-home)   HERMES_HOME_ARG="${2:?--hermes-home needs a directory}"; shift 2 ;;
    --plugins-root)  PLUGINS_ROOT="${2:?--plugins-root needs a directory}"; shift 2 ;;
    --profile)       PROFILE="${2:?--profile needs a name}"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "install.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

HERMES_HOME_DIR="${HERMES_HOME_ARG:-${HERMES_HOME:-${HOME}/.hermes}}"
ROOT="${PLUGINS_ROOT:-${HERMES_HOME_DIR}/plugins}"
DST="${ROOT}/hermes_router"

# Profile label: explicit env/flag first, path derivation only as a fallback.
if [[ -z "${PROFILE}" ]]; then
  if [[ "${HERMES_HOME_DIR%/}" == */profiles/* ]]; then
    PROFILE="$(basename "${HERMES_HOME_DIR%/}")"
  else
    PROFILE="unknown"
  fi
fi

if [[ ! -f "${SRC}/plugin.yaml" || ! -f "${SRC}/__init__.py" ]]; then
  echo "install.sh: ${SRC} does not look like the hermes-router checkout" >&2
  exit 1
fi

# One exclusion list, used by BOTH the plan count and the copy, so the number
# printed in --dry-run is the number of files the real run writes.
list_src() {
  ( cd "${SRC}" && find . \
      \( -path ./.git -o -path ./tests -o -path ./benchmarks \
         -o -name __pycache__ -o -name '*.pyc' \) -prune \
      -o -type f -print )
}

src_list="$(list_src)"
n_files="$(printf '%s\n' "${src_list}" | grep -c . || true)"
n_bytes="$(printf '%s\n' "${src_list}" \
  | while IFS= read -r f; do [[ -n "$f" ]] && stat -c %s "${SRC}/${f}" 2>/dev/null; done \
  | awk '{s+=$1} END {printf "%d", s+0}')"

echo "source:   ${SRC}"
echo "target:   ${DST}"
echo "profile:  ${PROFILE}"
echo "files:    ${n_files} ($(( n_bytes / 1024 )) KiB, excluding .git tests benchmarks __pycache__)"

if [[ -d "${DST}" ]]; then
  echo "state:    re-install (target exists — files overwritten in place, nothing deleted)"
else
  echo "state:    fresh install"
fi

# Stale files are REPORTED, never deleted: a plugin directory may hold files a
# reviewer or an operator put there, and --delete over a live plugin dir is the
# failure mode this script exists to remove.
stale=""
if [[ -d "${DST}" ]]; then
  dst_list="$( cd "${DST}" && find . \
      \( -name __pycache__ -o -name '*.pyc' \) -prune -o -type f -print )"
  stale="$(comm -13 <(printf '%s\n' "${src_list}" | sort) \
                    <(printf '%s\n' "${dst_list}" | sort) | grep . || true)"
fi
if [[ -n "${stale}" ]]; then
  echo "stale:    $(printf '%s\n' "${stale}" | grep -c .) file(s) in the target that this checkout does not ship (left in place):"
  printf '%s\n' "${stale}" | head -10 | sed 's/^/            /'
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo
  echo "dry-run: nothing written. Re-run without --dry-run to install."
  exit 0
fi

mkdir -p "${DST}"
# tar (not rsync): merge, never delete. Same exclusions as the plan above.
tar -C "${SRC}" -cf - \
      --exclude=./.git --exclude=./tests --exclude=./benchmarks \
      --exclude='*/__pycache__' --exclude='*.pyc' . \
  | tar -C "${DST}" -xf -

for must in plugin.yaml __init__.py; do
  if [[ ! -f "${DST}/${must}" ]]; then
    echo "install.sh: copy finished but ${DST}/${must} is missing — install FAILED" >&2
    exit 1
  fi
done

echo
echo "installed ${n_files} files -> ${DST}"
echo "next:  hermes plugins enable hermes-router   # path key is hermes_router"
echo "then:  restart the Hermes gateway (INSTALL.md step 3) — profile: ${PROFILE}"
echo "check: hermes plugins list                   # then /router in chat"
