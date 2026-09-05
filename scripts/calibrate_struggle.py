#!/usr/bin/env python3
"""calibrate_struggle.py — F4 retro-calibration harness (v3.3.0 Phase 1).

Replays route-log events through the F1 struggle classifier to produce
per-profile infra/reasoning/ambiguous counts. READ-ONLY: never writes
config, never imports the gateway stack (pure log parser + classifier).

Data contract (reviewer-mandated — route logs carry NO error text):
  (a) FORWARD: Phase-1 route lines enriched at emit time with non-content
      skip evidence (http_status_class, finish_reason) — this harness reads
      those fields from struggle_shadow / route_skipped lines when present
      and calibrates FORWARD.
  (b) HISTORY (best-effort): join route_ids to session-store messages in
      profile state.db (messages table, read-only URI mode) — failure-shaped
      assistant rows nearest the route timestamp supply skip evidence for
      history the forward enrichment cannot cover.

Sources parsed (both):
  /tmp/uncensored-router-*.log            — route events (_log_route output)
  <profile_home>/logs/agent.log(.N)       — diagnostic evidence: anchor_route_failed
                                            reason=/finish= lines (python logger)
Rotated .1 files are included; malformed lines are skipped silently.

Scope correction (reviewer-verified 2026-09-05): today's anchor fires are ALL
mode=plan reason=complexity_stage1 — struggle never fired. The dominant
observed waste is anchored_call_failed RE-FIRE hammering (one route_id fired
29x into failing anchors). This harness therefore ALSO emits a log-only
counter: anchor re-fire suppression candidates keyed per (route_id, task_id)
— the candidate Phase 2 feature a struggle-keyed cooldown cannot see.

Usage:
  python3 calibrate_struggle.py [--since 2026-09-05] [--until 2026-09-06]
      [--route-glob '/tmp/uncensored-router-*.log']
      [--profiles-root /opt/data/profiles] [--json OUT.json]

Output: per-profile counts {infra, reasoning, ambiguous, no_evidence} + the
anchor re-fire table. --json dumps the full payload; stdout gets the summary.
Exit code 0 on success; 1 only on fatal usage errors (never on data gaps).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Plugin import (classifier only — NEVER the gateway stack)
# ---------------------------------------------------------------------------

_PLUGIN_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_PARENT not in sys.path:
    sys.path.insert(0, _PLUGIN_PARENT)

ROUTE_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+"
    r"(?P<stage>PRE|POST|SEMANTIC)\s+"
    r"(?P<rest>.*)$"
)
KV_RE = re.compile(r"(\w+)=(\[[^\]]*\]|\S+)")

DIAG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\s+"
    r"(?P<lvl>[A-Z]+)\s+\[(?P<session>[\w.-]+)\]\s+"
    r"(?P<logger>[\w.]+):\s+(?P<msg>.*)$"
)
_DIAG_ANCHOR_FAILED_RE = re.compile(r"anchor_route_failed\s+reason=(\w+)(?:\s+(?P<detail>.*))?")
_FINISH_RE = re.compile(r"finish=(\w+)")
_HTTP_CLASS_RE = re.compile(r"http_status_class=(\w+)")
_SESSION_TS_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_kv(rest: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in KV_RE.findall(rest or ""):
        out.setdefault(k, v)
    return out


def _route_ts(s: str) -> Optional[float]:
    try:
        import calendar
        import time as _t

        return calendar.timegm(_t.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001
        return None


def _diag_ts(s: str) -> Optional[float]:
    try:
        import calendar
        import time as _t

        return calendar.timegm(_t.strptime(s.split(",", 1)[0], _SESSION_TS_FMT))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Log discovery + parsing
# ---------------------------------------------------------------------------


def _iter_files(patterns: List[str]) -> List[str]:
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(pat)))
    # rotated .1 companions explicitly included (glob already catches *.log;
    # add the .1 variants for patterns ending in .log)
    for pat in list(patterns):
        if pat.endswith(".log"):
            files.extend(sorted(glob.glob(pat + ".1")))
    seen: set = set()
    out: List[str] = []
    for f in files:
        if f not in seen and os.path.isfile(f):
            seen.add(f)
            out.append(f)
    return out


def parse_route_logs(paths: List[str]) -> List[Dict[str, Any]]:
    """Parse _log_route lines. Malformed lines are skipped (torn writes)."""
    events: List[Dict[str, Any]] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    m = ROUTE_LINE_RE.match(line)
                    if not m:
                        continue  # malformed — tolerate
                    fields = _parse_kv(m.group("rest"))
                    detail = fields.get("event_detail", "")
                    events.append({
                        "ts_raw": m.group("ts"),
                        "ts": _route_ts(m.group("ts")),
                        "stage": m.group("stage"),
                        "event_detail": detail,
                        "fields": fields,
                        "source": os.path.basename(path),
                    })
        except OSError:
            continue  # unreadable file — skip
    return events


def parse_agent_logs(paths: List[str]) -> List[Dict[str, Any]]:
    """Parse agent.log diagnostic evidence: anchor_route_failed reason=/finish=
    lines (python-logger output — NOT in route logs; verified data contract)."""
    diags: List[Dict[str, Any]] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "anchor_route_failed" not in line:
                        continue
                    m = DIAG_LINE_RE.match(line.strip())
                    if not m:
                        continue  # malformed — tolerate
                    fm = _DIAG_ANCHOR_FAILED_RE.search(m.group("msg"))
                    if not fm:
                        continue
                    reason = fm.group(1) or ""
                    detail = fm.group("detail") or ""
                    fin = _FINISH_RE.search(detail)
                    diags.append({
                        "ts_raw": m.group("ts"),
                        "ts": _diag_ts(m.group("ts")),
                        "session_id": m.group("session"),
                        "reason": reason,
                        "finish_reason": fin.group(1) if fin else "",
                        "source": os.path.basename(path),
                    })
        except OSError:
            continue
    return diags


# ---------------------------------------------------------------------------
# Evidence join (session_id + timestamp proximity) + classification
# ---------------------------------------------------------------------------

_JOIN_WINDOW_S = 120.0


def _join_evidence(events: List[Dict[str, Any]],
                   diags: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Join route events to diagnostic lines by session_id + timestamp
    proximity (route lines alone are evidence-thin). Best-effort for history:
    the nearest anchor_route_failed line within _JOIN_WINDOW_S donates its
    reason/finish as non-content skip evidence."""
    joined: Dict[int, Dict[str, Any]] = {}
    for idx, ev in enumerate(events):
        sid = ev["fields"].get("session_id", "")
        ev_ts = ev.get("ts")
        if not sid or ev_ts is None:
            continue
        best: Optional[Dict[str, Any]] = None
        best_dt = _JOIN_WINDOW_S + 1
        for dg in diags:
            if dg.get("session_id") != sid or dg.get("ts") is None:
                continue
            dt = abs(dg["ts"] - ev_ts)
            if dt <= _JOIN_WINDOW_S and dt < best_dt:
                best, best_dt = dg, dt
        if best:
            joined[idx] = {"reason": best.get("reason", ""),
                           "finish_reason": best.get("finish_reason", "")}
    return joined


def _evidence_to_reason_code(ev: Dict[str, Any], fields: Dict[str, str]) -> str:
    """Map joined non-content evidence to a classifier reason_code. Only
    non-content fields are read (http_status_class, finish_reason, route
    reason) — never message content."""
    status_class = fields.get("http_status_class", "")
    finish = fields.get("finish_reason", "") or ev.get("finish_reason", "")
    if status_class in ("4xx", "5xx") or finish in ("content_filter",):
        return "repeated_same_failure"
    if fields.get("reason", "") in ("anchored_call_failed",):
        return "repeated_same_failure"
    return "user_struggle_signal"


def classify_events(events: List[Dict[str, Any]],
                    diags: List[Dict[str, Any]]) -> Tuple[Counter, List[Dict[str, Any]]]:
    """Replay each struggle-relevant route event through classify_struggle.
    Returns (kind counts, replay rows)."""
    from hermes_router import struggle_class

    joined = _join_evidence(events, diags)
    counts: Counter = Counter()
    rows: List[Dict[str, Any]] = []
    for idx, ev in enumerate(events):
        fields = ev["fields"]
        detail = ev["event_detail"]
        task_id = fields.get("task_id", "")
        if detail == "struggle_shadow":
            # Forward product: classification already computed at emit time.
            kind = fields.get("kind", "") or "ambiguous"
            counts[kind] += 1
            rows.append({"event": detail, "kind": kind, "forward": True,
                         "ts": ev["ts_raw"], "source": ev["source"]})
            continue
        if detail in ("route_skipped", "cap_blocked") and task_id:
            evidence = joined.get(idx, {})
            reason_code = _evidence_to_reason_code(evidence, fields)
            fake_task = "cal:" + (task_id or "anon")
            kind, _detail_label = struggle_class.classify_struggle(fake_task, reason_code)
            counts[kind] += 1
            rows.append({"event": detail, "kind": kind, "forward": False,
                         "evidence": evidence or {},
                         "ts": ev["ts_raw"], "source": ev["source"]})
            continue
        # struggle never fired for this event — counted, not classified
        counts["no_struggle_event"] += 1
    return counts, rows


# ---------------------------------------------------------------------------
# Anchor re-fire suppression candidates (log-only counter, reviewer scope fix)
# ---------------------------------------------------------------------------


def anchor_refire_candidates(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Count anchored_call_failed re-fires per anchor chain identity. Today's
    dominant waste (one ask hammered 29x into failing anchors) — invisible to
    a struggle-keyed cooldown; v3.3.1 shipped the failure-backoff ledger as
    the suppression feature.

    Keying (verified against live logs 2026-09-05): route_skipped lines carry
    route_id + session_id but NO task_id; route_id differs per PRE re-fire
    (task_id prefix + fire timestamp), so re-fire grouping keys on the
    route_id PREFIX (task hash component) + session_id — the stable identity
    of "the same ask re-fired". task_id is donated from the matching
    anchor_route_fired line when present."""
    fires: Counter = Counter()
    task_by_prefix: Dict[str, str] = {}
    for ev in events:
        fields = ev["fields"]
        if ev["event_detail"] == "anchor_route_fired":
            rid = fields.get("route_id", "")
            prefix = rid.rsplit("-", 1)[0] if "-" in rid else rid
            if prefix and fields.get("task_id"):
                task_by_prefix.setdefault(prefix, fields["task_id"])
    for ev in events:
        fields = ev["fields"]
        if ev["event_detail"] == "route_skipped" and \
                fields.get("reason", "") == "anchored_call_failed":
            rid = fields.get("route_id", "")
            prefix = rid.rsplit("-", 1)[0] if "-" in rid else rid
            fires[(prefix, fields.get("session_id", ""))] += 1
    rows = []
    for (prefix, sid), n in fires.items():
        if n >= 2:
            rows.append({"route_id_prefix": prefix, "session_id": sid,
                         "task_id": task_by_prefix.get(prefix, ""),
                         "refires": n})
    rows.sort(key=lambda r: -r["refires"])
    return rows


# ---------------------------------------------------------------------------
# v3.3.1 anchor_backoff_blocked counter (the suppressed re-fire total)
# ---------------------------------------------------------------------------


def anchor_backoff_blocked_count(events: List[Dict[str, Any]]) -> int:
    """Count anchor_backoff_blocked log lines (v3.3.1 F2): each line is a
    staging attempt the backoff ledger suppressed — the "would-have-wasted"
    counter (an anchor attempt + its cap estimate NOT spent). Never raises."""
    try:
        return sum(1 for ev in events
                   if ev.get("event_detail") == "anchor_backoff_blocked")
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# state.db best-effort history join (data contract (b))
# ---------------------------------------------------------------------------


def state_db_evidence(profiles_root: str, events: List[Dict[str, Any]]) -> Dict[str, int]:
    """Best-effort: for route events whose profile home has a state.db, join
    session_id to the nearest failure-shaped assistant row. Returns counts of
    how many route events gained evidence this way. Read-only; never raises."""
    counts = {"joined": 0, "no_db": 0}
    session_ids = {ev["fields"].get("session_id", "") for ev in events}
    session_ids.discard("")
    for sid in session_ids:
        db = None
        for prof in sorted(glob.glob(os.path.join(profiles_root, "*"))):
            cand = os.path.join(prof, "state.db")
            if not os.path.isfile(cand):
                continue
            db = cand
            break  # single-tenant host: first state.db is the profile store
        if not db:
            counts["no_db"] += 1
            continue
        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
            try:
                conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'assistant'",
                    (sid,),
                ).fetchone()
            finally:
                conn.close()
            counts["joined"] += 1
        except Exception:  # noqa: BLE001
            continue
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="F4 struggle retro-calibration (read-only)")
    ap.add_argument("--since", default="", help="YYYY-MM-DD start (inclusive)")
    ap.add_argument("--until", default="", help="YYYY-MM-DD end (inclusive)")
    ap.add_argument("--route-glob", default="/tmp/uncensored-router-*.log")
    ap.add_argument("--agent-glob", default="/opt/data/profiles/*/logs/agent.log")
    ap.add_argument("--profiles-root", default="/opt/data/profiles")
    ap.add_argument("--json", default="", help="write full payload JSON here")
    args = ap.parse_args(argv)

    route_files = _iter_files([args.route_glob])
    agent_files = _iter_files([args.agent_glob])
    events = parse_route_logs(route_files)
    diags = parse_agent_logs(agent_files)

    if args.since:
        import calendar
        import time as _t

        cutoff = calendar.timegm(_t.strptime(args.since, "%Y-%m-%d"))
        events = [e for e in events if e.get("ts") is not None and e["ts"] >= cutoff]
        diags = [d for d in diags if d.get("ts") is not None and d["ts"] >= cutoff]
    if args.until:
        import calendar
        import time as _t

        cutoff = calendar.timegm(_t.strptime(args.until, "%Y-%m-%d")) + 86400
        events = [e for e in events if e.get("ts") is not None and e["ts"] < cutoff]
        diags = [d for d in diags if d.get("ts") is not None and d["ts"] < cutoff]

    counts, rows = classify_events(events, diags)
    refires = anchor_refire_candidates(events)
    sdb = state_db_evidence(args.profiles_root, events)
    backoff_blocked = anchor_backoff_blocked_count(events)

    summary = {
        "route_files": len(route_files),
        "agent_files": len(agent_files),
        "route_events": len(events),
        "diag_lines": len(diags),
        "kinds": dict(counts),
        "anchor_refire_candidates": refires[:20],
        "anchor_refire_total_routes": sum(1 for r in refires),
        "anchor_backoff_blocked_total": backoff_blocked,
        "state_db_join": sdb,
        "note": ("Phase 1 validates detector feeding (forward-enriched "
                 "struggle_shadow lines), not infra percentages — conductor "
                 "prediction retracted as unfalsifiable-as-specced."),
    }
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump({"summary": summary, "replay_rows": rows}, fh,
                          indent=1, ensure_ascii=False)
        except OSError as exc:
            print(f"warn: json dump failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())