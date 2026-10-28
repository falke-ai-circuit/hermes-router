#!/usr/bin/env python3
"""R76-P2-D4/D5 inertness probe.

Runs the SAME drive against a given tree (parent or HEAD) and prints a JSON
snapshot.  Nothing here is tree-specific except which symbols exist.
"""
import hashlib
import json
import os
import sys
import tempfile

TREE = sys.argv[1]
OUT = {"tree": TREE}

import hermes_router.suggestions as S  # noqa: E402
import hermes_router.route_gate as G  # noqa: E402


def norm(rec):
    if isinstance(rec, dict):
        return {k: norm(v) for k, v in sorted(rec.items()) if k != "ts"}
    if isinstance(rec, list):
        return [norm(v) for v in rec]
    return rec


# ---------------- D4: suggestions / budget ledger ----------------
tmpd = tempfile.mkdtemp(prefix="d45-")
ledger = os.path.join(tmpd, "budget.jsonl")
S._store_path = lambda: ledger
S.clear_for_tests()

d4 = {}
d4["symbols_present"] = {n: hasattr(S, n) for n in
                         ("mark_turn_pending", "pending_task_for",
                          "record_sugg_skipped", "gate_delivered_count")}
try:
    seqs = [S.record_sugg_fired("t1", "pre") for _ in range(3)]
    d4["fired_seqs"] = seqs
    d4["delivered"] = [S.record_sugg_delivered("t1", "pre", sq) for sq in seqs]
    d4["would_spend_before_commit"] = S.would_spend("t1", "pre")
    d4["committed"] = S.commit_turn("t1")
    d4["would_spend_after_commit"] = S.would_spend("t1", "pre")
    d4["committed_count"] = S.committed_count("t1")
    if not os.environ.get("NO_DIRECT"):
        if hasattr(S, "record_sugg_skipped"):
            d4["skip_write"] = S.record_sugg_skipped("t1", "pre", 99, "timeout")
            d4["skip_bad_gate"] = S.record_sugg_skipped("t1", "nope", 98, "x")
        if hasattr(S, "gate_delivered_count"):
            d4["gate_delivered_count"] = S.gate_delivered_count("t1", "pre")
    # replay from disk (fresh process-state, same file)
    S.clear_for_tests()
    d4["committed_count_after_replay"] = S.committed_count("t1")
    d4["would_spend_after_replay"] = S.would_spend("t1", "pre")
    if hasattr(S, "mark_turn_pending") and not os.environ.get("NO_DIRECT"):
        S.mark_turn_pending("task-1", "sess-1", "turn-1")
        d4["pending_lookup"] = S.pending_task_for("sess-1", "turn-1")
        d4["pending_lookup_absent"] = S.pending_task_for("sess-1", "turn-9")
    d4["pending_map_len_after_clear"] = len(S._pending_turns) if hasattr(S, "_pending_turns") else "n/a"
    d4["clear_for_tests_ok"] = (S.clear_for_tests() is None)
    raw = open(ledger, "rb").read().decode("utf-8") if os.path.exists(ledger) else ""
    recs = [json.loads(l) for l in raw.splitlines() if l.strip()]
    stripped = [{k: v for k, v in r.items() if k != "ts"} for r in recs]
    d4["ledger_rows"] = len(recs)
    d4["ledger_events"] = [r.get("event") for r in recs]
    d4["ledger_sha256_ts_stripped"] = hashlib.sha256(
        json.dumps(stripped, sort_keys=True).encode()).hexdigest()
except Exception as exc:  # noqa: BLE001
    d4["ERROR"] = "%s: %s" % (type(exc).__name__, exc)
OUT["d4_suggestions"] = d4

# ---------------- D5: route_gate turn-claim state ----------------
try:
    G.clear_turn_claims()
    G.clear_declared("s1")
    G.clear_declared("s2")
    d5 = {}
    d5["symbols_present"] = {n: hasattr(G, n) for n in ("_TURN_CLAIMS", "record_turn_claim")}
    d5["register_declared_1"] = G.register_declared("s1", G.LANE_SHADOW, "declared_user")
    d5["claim_after_register"] = norm(G.claim_state("s1"))
    G.stamp_turn_claim("s1", G.LANE_HIGHER_PRE, "declared_user")
    d5["claim_after_stamp"] = norm(G.claim_state("s1"))
    G.mark_turn_claim_executed("s1")
    d5["claim_after_executed"] = norm(G.claim_state("s1"))
    G.mark_shadow_rendered("s1")
    d5["claim_after_mark_rendered"] = norm(G.claim_state("s1"))
    # the ONLY documented body of record_turn_claim == register_declared
    if hasattr(G, "record_turn_claim"):
        G.record_turn_claim("s2", G.LANE_DECISION, "auto_lane")
        d5["equiv_via_record_turn_claim"] = norm(G.claim_state("s2"))
    else:
        G.register_declared("s2", G.LANE_DECISION, "auto_lane")
        d5["equiv_via_record_turn_claim"] = norm(G.claim_state("s2"))
    G.clear_turn_claims("s1")
    d5["claim_after_clear_s1"] = norm(G.claim_state("s1"))
    d5["claim_after_clear_s2"] = norm(G.claim_state("s2"))
    # module-global dict census: any state container still exposed
    d5["turn_claim_registry_size"] = len(G._TURN_CLAIMED)
    d5["declared_claims_size"] = len(G._DECLARED_CLAIMS)
    OUT["d5_route_gate"] = d5
except Exception as exc:  # noqa: BLE001
    OUT["d5_route_gate"] = {"ERROR": "%s: %s" % (type(exc).__name__, exc)}

print(json.dumps(OUT, indent=2, sort_keys=True, default=str))
