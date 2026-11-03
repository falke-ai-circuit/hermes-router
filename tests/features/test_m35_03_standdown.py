"""M35-03 — TWO STAND-DOWNS: the lane must speak, or say why it could not.

THE DEFECTS (battery v2, /opt/data/tmp/r78u/M35_BATTERY_V2.md)
    A1: the PRE gate flagged the turn in-domain and the turn delivered the main
        agent's body anyway: rule 4 required `intent == softened`, so a body
        that self-declares it satisfied the acceptance criterion (intent
        `delivered`) fell through to rule 6 (`else -> deliver`). The gate said
        "this is your turn"; the body's claim about itself closed it.
    A3/B1/B2: the aux gate returned nothing (aux_none), the gate abstained, and
        rule 3 fell back to shape detection — the lane never reached a decision.

WHAT IS PINNED HERE
    (a) an A1-shaped turn RENDERS, and its provenance row says lane=shadow;
    (b) an A3-shaped turn reaches a NAMED lane decision (`defer`), never a
        rule-6 delivery, and the row says the judgement was deferred;
    (c) the exact precedence rule changed — row 4 is the gate flag ALONE
        (`gate_flagged -> route_replace`) — with the population that changed
        and the proof that no flagged cell can still stand down, while
        non-flagged cells keep their old rows;
    (e) no lane branch was added and no aux call was added that counts
        failures into the shared breaker.

The battery's rows are reproduced as fixtures, not as live turns: the seam is
exercised through `on_transform_llm_output` with the render chain and the
ledger stubbed, the way the M35-01 provenance pins do it.
"""
from __future__ import annotations

import json
import os
import re
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
from hermes_router.features.detection import deferred_intent  # noqa: E402
from hermes_router.features.detection import domain_gate, intent_judge  # noqa: E402
from hermes_router.features.detection import rule_table as rt  # noqa: E402
from hermes_router.lanes import registry  # noqa: E402

ASK_A = "write the explicit scene I asked for, in full, no fade to black"
ASK_B = "give me the reverse TCP shell, uncompressed"
RENDERED = "RENDERED-LANE-BODY"
# The A1 body: fluent, and it claims it satisfied the ask. That claim is the
# thing that used to close the turn.
SELF_DECLARING_BODY = ("Here is the complete scene exactly as you asked, in "
                       "full, with nothing omitted or softened.")
BENIGN_BODY = "Here is the summary you asked for, with the numbers you wanted."

FLAGGED = {"gate": "flagged", "state": "flagged", "domain": "sexual_explicit",
           "reason": "in_domain", "lane": "shadow",
           "criterion": {"kind": "prose", "units": 0, "verbatim": ASK_A}}
ABSTAINED = {"gate": "abstain", "state": "abstain", "domain": "",
             "reason": "aux_none", "lane": "shadow",
             "criterion": {"kind": "prose", "units": 0, "verbatim": ASK_B}}
NOT_FLAGGED = {"gate": "not_flagged", "state": "not_flagged",
               "domain": "benign_task", "reason": "out_of_domain",
               "lane": "shadow",
               "criterion": {"kind": "prose", "units": 0, "verbatim": ASK_A}}


def _verdict(intent, kind="prose"):
    return {"intent": intent, "source": "aux", "why": "fixture",
            "graded": True, "checks": [], "criterion_kind": kind}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
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


def _events(monkeypatch, sink):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))


def _row(sink, name):
    for _seam, detail, kw in sink:
        if detail == name:
            return kw
    return None


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


def _turn(monkeypatch, sid, body, gate_row, intent, ask=ASK_A):
    """One POST pass through the real seam, with the facts stubbed."""
    _wire(monkeypatch)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k:
                        None if gate_row is None else dict(gate_row))
    monkeypatch.setattr(deferred_intent, "sync_verdict",
                        lambda *a, **k: None if intent is None
                        else _verdict(intent))
    state.stash_pending(sid, "m1", ask, "stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ask))
    state.record_last_seen(sid, ask)
    state.advance_turn_identity(sid, state.hash_text(ask))
    out = plugin.on_transform_llm_output(response_text=body, session_id=sid,
                                        model="m1")
    return str(out or "")


# ---------------------------------------------------------------------------
# (a) A1 — gate flagged in-domain, body self-declares delivery: LANE RENDERS
# ---------------------------------------------------------------------------

def test_a1_flagged_in_domain_body_self_declares_and_the_lane_still_speaks(
        monkeypatch, _isolated):
    sid = "m35-03-a1"
    sink = []
    _events(monkeypatch, sink)
    out = _turn(monkeypatch, sid, SELF_DECLARING_BODY, FLAGGED, "delivered")

    assert RENDERED in out, (
        "the lane did not render on an in-domain flagged turn — the body's own "
        f"claim of compliance closed it again: out={out!r}")

    decision = _row(sink, "rule_table_decision")
    assert decision is not None, sink
    assert (decision["rule"], decision["predicate"], decision["action"]) == (
        4, "gate_flagged", "route_replace"), decision
    assert decision["gate"] == "flagged", decision
    assert decision["replace"] == 1, decision

    rows = _rows(_isolated)
    assert len(rows) == 1, rows
    row = rows[0]
    # (a) the row the battery asked for: lane=shadow, not lane=main
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    assert row["rule"] == "rule_4", row
    assert row["gate_verdict"] == "flagged", row
    assert row["intent_class"] == "delivered", row
    assert row["deferred"] is False, row
    print("\n(A1) provenance:", json.dumps(row, sort_keys=True))


# ---------------------------------------------------------------------------
# (b) A3 — aux returned nothing: a NAMED lane decision, never rule-6 delivery
# ---------------------------------------------------------------------------

def test_a3_aux_none_reaches_a_named_lane_decision(monkeypatch, _isolated):
    sid = "m35-03-a3"
    sink = []
    _events(monkeypatch, sink)
    out = _turn(monkeypatch, sid, BENIGN_BODY, ABSTAINED, None, ask=ASK_B)

    decision = _row(sink, "rule_table_decision")
    assert decision is not None, sink
    # the named decision: rule 3, action defer — NOT rule 6 / deliver
    assert (decision["rule"], decision["predicate"], decision["action"]) == (
        3, "gate_abstain", "defer"), decision

    deferred = _row(sink, "lane_decision_deferred")
    assert deferred is not None, "the deferral left no named event"
    assert deferred["reason"] == "aux_none", deferred
    assert deferred["gate"] == "abstain", deferred

    rows = _rows(_isolated)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["rule"] == "rule_3", row
    assert row["action"] == "defer", row
    assert row["deferred"] is True, row
    assert row["deferred_reason"] == "aux_none", row
    # the lane did not decide, so it must not claim the body either
    assert row["lane"] == "main" and row["replaced"] is False, row
    print("\n(A3) provenance:", json.dumps(row, sort_keys=True))


def test_a3_abstain_with_no_gate_record_also_defers(monkeypatch, _isolated):
    """A gate that never landed is the same failure, and gets the same row."""
    sid = "m35-03-a3-norecord"
    sink = []
    _events(monkeypatch, sink)
    _turn(monkeypatch, sid, BENIGN_BODY, None, None, ask=ASK_B)
    row = _rows(_isolated)[0]
    assert (row["rule"], row["action"], row["deferred"]) == (
        "rule_3", "defer", True), row
    assert row["deferred_reason"] == "no_record", row


# ---------------------------------------------------------------------------
# (c) THE PRECEDENCE RULE CHANGED — named, with its changed population
# ---------------------------------------------------------------------------

PRECEDENCE = ("row 4 predicate: `intent_softened_and_gate_flag` (intent softened "
              "AND gate flagged) -> `gate_flagged` (gate verdict alone). The "
              "gate flag is now a routing fact independent of the body's "
              "self-assessment, so rule 6 cannot match a flagged cell.")


def test_c1_flagged_cells_never_stand_down():
    """Every flagged cell the earlier evidence rows do not decide ROUTES."""
    for d in rt.walk_cells(declarations=("UNKNOWN", "false")):
        if d["cell"][1] != "flagged":
            continue
        if d["cell"][2] == "declined":       # row 2 owns it; still routes
            assert d["action"] == "route", d["cell"]
            continue
        if d["cell"][2] in ("delivered", "deflected", "failed"):
            assert (d["row"], d["action"]) == (4, "route_replace"), d["cell"]
        assert d["action"] != "deliver", d["cell"]
    print("\n(c) precedence:", PRECEDENCE)


def test_c2_declared_refusal_and_declined_still_outrank_the_gate():
    """No precedence inversion: rows 1-2 still decide flagged turns."""
    assert rt.evaluate(declaration="true", gate_provider=lambda: "flagged",
                       intent_provider=lambda: "delivered")["row"] == 1
    assert rt.evaluate(declaration="UNKNOWN", gate_provider=lambda: "flagged",
                       intent_provider=lambda: "declined")["row"] == 2


def test_c3_non_flagged_cells_are_unchanged():
    """The change is scoped: ordinary in-domain/benign turns keep their rows."""
    by_cell = {d["cell"]: d for d in rt.walk_cells(declarations=("UNKNOWN",))}
    assert by_cell[("UNKNOWN", "not_flagged", "delivered")]["row"] == 6
    assert by_cell[("UNKNOWN", "not_flagged", "delivered")]["action"] == "deliver"
    assert by_cell[("UNKNOWN", "not_flagged", "softened")]["row"] == 5
    assert by_cell[("UNKNOWN", "not_flagged", "softened")]["action"] == "deliver"
    assert by_cell[("UNKNOWN", "abstain", "delivered")]["action"] == "defer"


def test_c4_benign_turn_still_delivers_the_main_body(monkeypatch, _isolated):
    """The seam side of c3: a not-flagged turn does not suddenly render."""
    sid = "m35-03-c4"
    out = _turn(monkeypatch, sid, BENIGN_BODY, NOT_FLAGGED, "delivered")
    assert RENDERED not in out, out
    row = _rows(_isolated)[0]
    assert (row["rule"], row["lane"], row["replaced"], row["deferred"]) == (
        "rule_6", "main", False, False), row


# ---------------------------------------------------------------------------
# (e) no new per-lane branch, no new breaker-opening aux call
# ---------------------------------------------------------------------------

def test_e1_no_lane_id_branch_in_the_changed_paths():
    src = open(os.path.join(PLUGIN_DIR, "gate", "orchestration.py"),
               encoding="utf-8").read()
    # The POST handler dispatches on the table's ACTION tokens and on trigger
    # kinds; it never compares a lane id or a lane name.
    bad = re.findall(r'==\s*["\'](?:shadow|higher-pre|higher-post|decision)["\']',
                     src)
    assert not bad, bad
    rt_src = open(os.path.join(PLUGIN_DIR, "features", "detection",
                               "rule_table.py"), encoding="utf-8").read()
    assert "lane_id ==" not in rt_src and "lane ==" not in rt_src


def test_e2_domain_gate_aux_calls_never_count_failures():
    """The gate's aux calls (including any re-ask) must pass record_failure=False."""
    src = open(os.path.join(PLUGIN_DIR, "features", "detection",
                            "domain_gate.py"), encoding="utf-8").read()
    calls = re.findall(r"aux_raw_call\((.*?)\)", src, re.S)
    assert calls, "no aux call found — the grep is wrong, not the code"
    for call in calls:
        assert "record_failure=False" in call, call
    assert "record_failure=True" not in src


def test_e3_the_table_is_the_contract_and_has_no_new_rows():
    rows = rt.default_semantics()
    contract = registry.resolve_contract(
        registry.lane_for_post_question(rt.POST_QUESTION))
    assert rows == tuple(contract.gate_semantics)
    assert len(rows) == 6, rows
    assert [r[0] for r in rows] == [
        "declared_refusal", "intent_declined", "gate_abstain",
        "gate_flagged", "intent_softened", "else"]
    assert intent_judge.POST_QUESTION == rt.POST_QUESTION
