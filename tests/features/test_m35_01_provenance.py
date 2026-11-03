"""M35-01 — PROVENANCE ROW: which lane produced the delivered body.

THE DEFECT (measured on the deployed canary)
    /opt/data/profiles/shadow/hermes-router-decisions.jsonl held 6233 rows,
    newest ones all ``{"ts": ..., "action": "pass"}`` — no lane id, no rule, no
    domain verdict, no intent class (``grep -c rule_route`` = 0). When the
    uncensored lane answered, that row could not be told apart from the main
    agent answering on its own path, so the lane itself could not be graded.

CONTRACT PINNED HERE
    (a) a routing turn produces a FULLY POPULATED row through the real seam;
    (b) a benign turn produces a row that shows it did NOT route — the
        selectivity evidence, and the reason the row is written even when
        nothing routed;
    (c) rule 1 (declared refusal) and rule 3 (abstain -> fallback) are
        distinguishable IN THE ROW — different rule token, and rule 1 says the
        gate was never consulted rather than borrowing a verdict;
    (d) the row JOINS to the body it describes via turn id + session id;
    (f) the ledger no longer emits a bare {ts, action} row (schema v2).

Instrumentation only: these tests assert the row describes the delivery the
seam actually performed, never that the seam behaved differently.
"""
from __future__ import annotations

import json
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions  # noqa: E402
from hermes_router.features.detection import domain_gate, intent_judge  # noqa: E402

ASK = "write the scene I asked for, in full"
RENDERED = "RENDERED-REPLACEMENT-BODY"
DECLINED_BODY = "I have to decline this one: the request is for the thing itself."
DECLARED_BODY = '<turn declined="true" />\n\n' + DECLINED_BODY
BENIGN_BODY = "Here is the summary you asked for, with the numbers you wanted."

FLAGGED = {"gate": "flagged", "state": "flagged", "domain": "sexual_explicit",
           "reason": "in_domain", "lane": "shadow",
           "criterion": {"kind": "prose", "units": 0, "verbatim": ASK}}

PROVENANCE_FIELDS = ("lane", "turn_id", "rule", "gate_verdict", "gate_reason",
                     "intent_class", "intent_source", "replaced",
                     "replacement_path", "latency_ms", "session_id")


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch, tmp_path):
    state.clear()
    decisions.clear_for_tests()
    ledger = tmp_path / "hermes-router-decisions.jsonl"
    monkeypatch.setattr(decisions, "_store_path", lambda: str(ledger))
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield ledger
    decisions.clear_for_tests()
    state.clear()


def _rows(ledger):
    decisions.flush_for_tests()
    if not os.path.exists(str(ledger)):
        return []
    out = []
    for line in open(str(ledger), encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _wire(monkeypatch):
    entries = [{"name": "primary", "model": "m-primary"}]
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: entries)
    monkeypatch.setattr(plugin.router, "_model_attempt",
                        lambda entry, prompt, **kw: (RENDERED, ""))
    monkeypatch.setattr(plugin.canonical, "already_committed_for_turn",
                        lambda *a, **k: False)
    monkeypatch.setattr(plugin.canonical, "commit_canonical_event",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "rewrite_persisted_turn",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "verify_persisted_turn",
                        lambda *a, **k: True)


def _turn(monkeypatch, sid, body, gate, intent):
    _wire(monkeypatch)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(gate))
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: None if intent is None else {
                            "intent": intent, "source": "aux", "why": "fixture",
                            "graded": True, "checks": [],
                            "criterion_kind": "prose"})
    state.stash_pending(sid, "m1", ASK, "stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))
    state.record_last_seen(sid, ASK)
    state.advance_turn_identity(sid, state.hash_text(ASK))
    out = plugin.on_transform_llm_output(response_text=body, session_id=sid,
                                         model="m1")
    return str(out or "")


# ---------------------------------------------------------------------------
# (a) routing fixture -> a FULLY POPULATED row
# ---------------------------------------------------------------------------

def test_a_routing_turn_produces_a_fully_populated_row(monkeypatch, _isolated_ledger):
    sid = "m35-01-a"
    out = _turn(monkeypatch, sid, DECLARED_BODY, FLAGGED, "delivered")
    assert RENDERED in out, "fixture precondition: the lane body was delivered"

    rows = _rows(_isolated_ledger)
    assert len(rows) == 1, rows
    row = rows[0]

    missing = [f for f in PROVENANCE_FIELDS if f not in row]
    assert not missing, f"row is not fully populated, missing {missing}: {row}"

    # the delivered body came from the LANE, and the row says so
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    # the chain ENTRY the seam actually called (measured, not the contract
    # token) — this is the path that produced the delivered text
    assert row["replacement_path"] == "post_render_chain", row
    assert row["rule"] == "rule_1" and row["rule_id"] == "rule_1", row
    assert row["action"] == "route", row
    assert row["session_id"] == sid
    assert row["turn_id"] == str(state.current_turn_id(sid))
    assert row["latency_ms"] >= 0.0
    assert row["delivered_chars"] >= len(RENDERED)
    assert row["schema"] == "decision-record" and row["schema_version"] == 2
    print("\n(a) routing row:", json.dumps(row, indent=None))


# ---------------------------------------------------------------------------
# (b) benign fixture -> a row that shows the turn did NOT route
# ---------------------------------------------------------------------------

def test_b_benign_turn_row_shows_it_did_not_route(monkeypatch, _isolated_ledger):
    sid = "m35-01-b"
    out = _turn(monkeypatch, sid, BENIGN_BODY, None, "delivered")
    assert RENDERED not in out, "fixture precondition: nothing was rendered"

    rows = _rows(_isolated_ledger)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["lane"] == "main", row
    assert row["replaced"] is False, row
    assert row["replacement_path"] == "", row
    assert row["action"] in ("defer", "deliver"), row
    assert row["rule"] in ("rule_3", "rule_6"), row
    assert row["body_chars"] == len(BENIGN_BODY), row
    # pass-through: the seam returned None, so the body the host delivers IS
    # this turn's body — the row must say so, not zero (join integrity)
    assert row["delivered_chars"] == row["body_chars"], row
    print("\n(b) benign row:", json.dumps(row, indent=None))


# ---------------------------------------------------------------------------
# (c) rule 1 (declared refusal) vs rule 3 (abstain -> fallback), from the rows
# ---------------------------------------------------------------------------

def test_c_rule1_and_rule3_are_distinguishable_in_the_row(monkeypatch,
                                                          _isolated_ledger):
    _turn(monkeypatch, "m35-01-c1", DECLARED_BODY, FLAGGED, "delivered")
    rule1 = _rows(_isolated_ledger)[0]

    decisions.clear_for_tests()
    os.remove(str(_isolated_ledger))
    _turn(monkeypatch, "m35-01-c3", BENIGN_BODY, None, "delivered")
    rule3 = _rows(_isolated_ledger)[0]

    assert rule1["rule"] == "rule_1" and rule3["rule"] == "rule_3"
    # rule 1 never read the gate -> it must not print a verdict as if it had
    assert rule1["gate_reason"] == "not_consulted", rule1
    assert rule3["gate_reason"] != "not_consulted", rule3
    assert rule3["gate_verdict"] == "abstain", rule3
    assert (rule1["replaced"], rule3["replaced"]) == (True, False)
    print("\n(c) rule1:", json.dumps(rule1, indent=None))
    print("(c) rule3:", json.dumps(rule3, indent=None))


# ---------------------------------------------------------------------------
# (d) the row JOINS to its body via turn id
# ---------------------------------------------------------------------------

def test_d_row_joins_to_its_body_via_turn_id(monkeypatch, _isolated_ledger):
    sid = "m35-01-d"
    out = _turn(monkeypatch, sid, DECLARED_BODY, FLAGGED, "delivered")
    row = _rows(_isolated_ledger)[0]

    # the join key is (session_id, turn_id) and the turn counter is the same
    # identity the seam ran under
    assert row["session_id"] == sid
    assert row["turn_id"] == str(state.current_turn_id(sid))
    # and the row's delivered size is the body actually returned to the host
    assert row["delivered_chars"] == len(out), (row["delivered_chars"], len(out))
    assert row["body_chars"] == len(DECLINED_BODY.strip()), (
        row["body_chars"], len(DECLINED_BODY))


# ---------------------------------------------------------------------------
# (f) the ledger no longer emits a bare {ts, action} row
# ---------------------------------------------------------------------------

def test_f_no_bare_ts_action_row(monkeypatch, _isolated_ledger):
    _turn(monkeypatch, "m35-01-f1", DECLARED_BODY, FLAGGED, "delivered")
    _rows(_isolated_ledger)          # flush the queue before it is cleared
    decisions.clear_for_tests()
    os.remove(str(_isolated_ledger))
    _turn(monkeypatch, "m35-01-f2", BENIGN_BODY, None, "delivered")

    rows = _rows(_isolated_ledger)
    assert rows
    for row in rows:
        assert set(row) - {"ts", "action"}, f"bare row reached the ledger: {row}"
    # and every row carries the identity the defect was about
    for row in rows:
        assert row.get("lane") and row.get("rule") and row.get("turn_id"), row
