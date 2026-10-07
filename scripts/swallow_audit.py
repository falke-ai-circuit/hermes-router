#!/usr/bin/env python3
"""swallow_audit.py — P6 (proposal §2.5): per-module silent-swallow counter.

Walks every package module, counts `except Exception` handler sites that
are SILENT (no log call, no telemetry.swallow/isolate call, no re-raise)
inside the handler body. The per-module counts are the baseline
(tests/swallow_baseline.json, recorded at P6); the audit FAILS when any
module's silent count EXCEEDS its baseline (regression-only — §2.5:
"only regressions fail"; migrated sites lower counts, never raise them).

Usage:
    python3 scripts/swallow_audit.py [--update-baseline]
"""
from __future__ import annotations

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "hermes_router") if os.path.isdir(
    os.path.join(ROOT, "hermes_router")) else ROOT
BASELINE = os.path.join(ROOT, "tests", "swallow_baseline.json")

_NOISY_NAMES = {"logger", "log", "_log_route", "log_route", "logging",
                "isolate", "record_swallow", "l", "lg", "_swallow"}


def _silent_handlers(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # only bare/broad exception handlers count as swallow candidates
        if not (node.type is None or
                (isinstance(node.type, ast.Name) and node.type.id in
                 ("Exception", "BaseException"))):
            continue
        if not _is_silent(node):
            continue
        count += 1
    return count


def _is_silent(handler: ast.ExceptHandler) -> bool:
    for n in ast.walk(handler):
        if isinstance(n, ast.Raise):
            return False  # re-raise: not a swallow
        if isinstance(n, ast.Call):
            f = n.func
            name = getattr(f, "attr", getattr(f, "id", ""))
            base = getattr(f.value, "id", "") if isinstance(
                f, ast.Attribute) else ""
            if name in ("exception", "warning", "error", "info", "debug",
                        "critical", "log_route", "isolate", "record_swallow"):
                return False
            if base in _NOISY_NAMES:
                return False
    return True


def module_counts() -> "dict[str, int]":
    counts = {}
    for dirpath, _dirs, files in os.walk(PKG):
        if "__pycache__" in dirpath or os.path.sep + "tests" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, PKG)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except SyntaxError as exc:
                print("SYNTAX ERROR %s: %s" % (rel, exc), file=sys.stderr)
                counts[rel] = -1
                continue
            counts[rel] = _silent_handlers(tree)
    return counts


def main() -> int:
    counts = module_counts()
    if "--update-baseline" in sys.argv:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(counts, fh, indent=1, sort_keys=True)
        print("baseline updated: %d modules" % len(counts))
        return 0
    base = {}
    if os.path.exists(BASELINE):
        base = json.load(open(BASELINE, encoding="utf-8"))
    regressions = {m: (c, base.get(m, 0)) for m, c in sorted(counts.items())
                   if c > base.get(m, 0)}
    silent_total = sum(v for v in counts.values() if v > 0)
    if regressions:
        print("SWALLOW REGRESSIONS (count > baseline):")
        for m, (c, b) in regressions.items():
            print("  %s: %d > %d" % (m, c, b))
        return 1
    print("swallow_audit OK — %d modules, %d silent sites (baseline holds)"
          % (len(counts), silent_total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
