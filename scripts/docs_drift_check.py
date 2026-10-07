#!/usr/bin/env python3
"""docs-drift check: every module path named in the architecture.md module
table must exist in the tree. Fails (exit 1) on drift."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "architecture.md")

# ``module.py`` backticked rows in the module tables
PATH_RE = re.compile(r"`((?:core|features|passes|gate|api|lanes)/[A-Za-z0-9_./-]+\.py)`")
# bare top-level names in the table (e.g. `classifier.py`)
BARE_RE = re.compile(r"\| \| `([A-Za-z0-9_./-]+\.py)`")


def main():
    with open(DOC, encoding="utf-8") as fh:
        text = fh.read()
    missing = []
    prefixes = ["", "core/", "features/", "passes/", "gate/", "api/", "lanes/",
                "hermes_router/"]
    for m in PATH_RE.finditer(text):
        rel = m.group(1)
        if not any(os.path.exists(os.path.join(ROOT, p + rel)) for p in prefixes):
            missing.append(rel)
    for m in BARE_RE.finditer(text):
        rel = m.group(1)
        if not any(os.path.exists(os.path.join(ROOT, p + rel)) for p in prefixes):
            missing.append(rel)
    if missing:
        print("docs-drift: MISSING referenced modules:")
        for p in sorted(set(missing)):
            print("  -", p)
        return 1
    print("docs-drift: OK — every referenced module exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
