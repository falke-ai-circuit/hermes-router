#!/usr/bin/env bash
# scripts/ci.sh — P8b CI gate chain (Block C, roadmap).
# Usage: bash scripts/ci.sh            (run from repo root or anywhere)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FAILED=0

step() { echo "=== $1 ==="; }

step "ruff (errors-only gate; style classes are a future ratchet — see ruff.toml)"
if command -v ruff >/dev/null 2>&1; then
    ruff check . || FAILED=1
elif python3 -m ruff --version >/dev/null 2>&1; then
    python3 -m ruff check . || FAILED=1
else
    echo "SKIP: ruff not installed"
fi

step "pytest (mock suite, hermetic)"
python3 -m pytest tests -q -p no:cacheprovider -m "not live" || FAILED=1

step "swallow_audit (regression-only)"
python3 scripts/swallow_audit.py || FAILED=1

step "import-linter (layer check L0<-L1<-L2<-L3<-L4)"
if command -v lint-imports >/dev/null 2>&1; then
    lint-imports || FAILED=1
else
    echo "SKIP: import-linter not installed"
fi

step "docs-drift check (architecture.md module table vs tree)"
python3 scripts/docs_drift_check.py || FAILED=1

step "battery pointer"
echo "NOTE: the behavioral battery runs orchestrator-side against a live"
echo "canary profile (benchmarks/behavioral_battery.py) — it is NOT part"
echo "of CI; see benchmarks/METHODOLOGY.md before trusting a result."

if [ "$FAILED" -ne 0 ]; then
    echo "CI: FAIL"
    exit 1
fi
echo "CI: PASS"
