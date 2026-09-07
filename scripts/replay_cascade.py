#!/usr/bin/env python3
"""Counterfactual replay harness (v3.6 §11.4-KEEP-1 — THE instrument).

Replays logged turns through the OLD engine (the live deterministic
classifier, i.e. today's production baseline) vs the NEW cascade
(trigger_cascade.decide), and diffs the decisions to a report file. NO live
behavior change — this is offline instrumentation that decides the semantic
arm's fate (gray-zone FN volume at PRE, weak-compliance volume at POST).

Inputs (all optional, sensible defaults):
  --route-log <path>    uncensored-router route log (default: profile log)
  --limit <N>           max turns to replay (default 2000)
  --out <path>          report file (default /tmp/trigger_replay_report.md)

Old engine = hermes_router.classifier.scan_pre on the six PRE groups.
New engine = trigger_cascade.decide() with semantic OFF (default).

Report: simple counts + per-arm fire rates + diff table (§11.4: no metric
ceremony). Kill-criteria metrics (§11.6) reference the same numbers.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from typing import Dict, List, Optional, Tuple

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.dirname(_PLUGIN_DIR), _PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router.classifier as classifier  # noqa: E402
import hermes_router.trigger_cascade as cascade  # noqa: E402

_PRE_GROUPS = [n for n in classifier.PATTERN_GROUPS if n in classifier.PRE_GROUP_NAMES]

# route-log line: "2026-09-07T12:34:56Z PRE k=v k=v"
_LOG_LINE = re.compile(r"^(\S+)\s+(\S+)\s*(.*)$")
# recover the user text proxy: content_chars + session_id fields; full text is
# NOT in the route log (content-free by design), so the harness accepts an
# optional --fixtures dir of {text.txt} files for text-level replay, else
# diffs at the DECISION-COUNTER level using gate stats + flagged events.


def parse_log(path: str, limit: int) -> Dict[str, int]:
    counts: Counter = Counter()
    if not os.path.exists(path):
        return dict(counts)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()[-limit:]
    for line in lines:
        m = _LOG_LINE.match(line.strip())
        if not m:
            continue
        counts[m.group(2)] += 1
        fields = dict(tok.split("=", 1) for tok in m.group(3).split() if "=" in tok)
        pg = fields.get("pattern_groups", "")
        if pg:
            for g in pg.split(","):
                counts["old_pre_fire:" + g] += 1
    return dict(counts)


def replay_texts(texts: List[str]) -> Tuple[Counter, Counter, List[Dict[str, str]]]:
    """Text-level replay: old engine vs cascade per fixture text."""
    old_fires: Counter = Counter()
    new_actions: Counter = Counter()
    diffs: List[Dict[str, str]] = []
    for text in texts:
        old = classifier.scan_pre(text, patterns=_PRE_GROUPS)
        new = cascade.decide(text)
        for g in old:
            old_fires["old_fire:" + g] += 1
        new_actions["new_" + new["action"]] += 1
        old_action = "route" if old else "pass"
        if old_action != new["action"]:
            diffs.append({
                "text": text[:80].replace("\n", " "),
                "old": old_action + ("(" + ",".join(old) + ")" if old else ""),
                "new": new["action"] + "(" + new["gate_reason"] + ")",
            })
    return old_fires, new_actions, diffs


def main() -> int:
    ap = argparse.ArgumentParser(description="v3.6 counterfactual replay harness")
    ap.add_argument("--route-log", default=os.environ.get("HERMES_ROUTER_LOG", ""))
    ap.add_argument("--fixtures", default="", help="dir of .txt fixtures for text-level replay")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--out", default="/tmp/trigger_replay_report.md")
    args = ap.parse_args()

    lines: List[str] = ["# trigger-cascade counterfactual replay (§11.4)", ""]
    lines.append("detector: %s (semantic arm: %s)" % (
        cascade.DETECTOR_VERSION, "ON" if cascade.semantic_gate_enabled() else "OFF (default)"))

    # decision-counter level from the live route log
    if args.route_log and os.path.exists(args.route_log):
        counts = parse_log(args.route_log, args.limit)
        lines.append("")
        lines.append("## route-log event counters (last %d lines)" % args.limit)
        for k in sorted(counts):
            lines.append("- %s: %d" % (k, counts[k]))
    elif args.route_log:
        lines.append("")
        lines.append("route log not found: %s" % args.route_log)

    # text-level replay on fixtures
    if args.fixtures and os.path.isdir(args.fixtures):
        texts: List[str] = []
        for name in sorted(os.listdir(args.fixtures)):
            if name.endswith(".txt"):
                try:
                    with open(os.path.join(args.fixtures, name), "r",
                              encoding="utf-8", errors="replace") as fh:
                        texts.append(fh.read())
                except OSError:
                    continue
        old_fires, new_actions, diffs = replay_texts(texts)
        lines.append("")
        lines.append("## text-level replay (%d fixtures)" % len(texts))
        lines.append("- old fires: %s" % (dict(old_fires) or "{}"))
        lines.append("- new actions: %s" % (dict(new_actions) or "{}"))
        lines.append("- decision diffs: %d" % len(diffs))
        for d in diffs[:25]:
            lines.append("  - %r | old=%s | new=%s" % (d["text"], d["old"], d["new"]))
        grayzone = sum(1 for a in new_actions if a == "new_abstain")
        lines.append("")
        lines.append("gray-zone volume (cascade abstain on non-routed texts): %d/%d"
                     % (grayzone, len(texts)))

    lines.append("")
    lines.append("interpretation (§11.5): if gray-zone volume ≈ 0 and diffs ≈ 0, "
                 "the semantic stub stays deleted-by-default; regex + abstain contract suffice.")
    report = "\n".join(lines) + "\n"
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    print("report written: %s" % args.out)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
