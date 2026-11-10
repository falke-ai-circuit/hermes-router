"""M49-04 — coverage for the five gates M41-08 could not measure offline.

M41-08 (`/opt/data/tmp/r78u/M41-08_CORPUS.md`) closed with four gates
**UNMEASURABLE-OFFLINE** and one **PARTIALLY MEASURABLE**, because the aux
endpoint returned HTTP 402 for every call: 826/828 turns abstained with
`reason=aux_none`, so the lane's own decisions were never exercised.

The gates and what is asserted here instead:

  gate 1  in-domain detection >= 85%      -> the PRE record carries every
                                              field a live numerator needs
  gate 2  benign FP <= 5%                 -> same record; the abstain path
                                              carries a NAMED cause, so an
                                              abstain is never read as "safe"
  gate 3  judge test-retest >= 0.75       -> two identical judge calls return
                                              an identical verdict against a
                                              pinned prompt fingerprint
  gate 4  cost <= ~$0.0002/turn           -> the deterministic path spends no
                                              aux call at all (1.733 calls/turn
                                              is arithmetic, not a guess)
  gate 5  softened/failed body census     -> the rule census is computable from
                                              `walk_cells`, and a failed body is
                                              named and never delivered on a
                                              gate flag

Each test states what would make it red in its docstring. The corpus metrics
themselves stay unmeasurable without a live aux endpoint — these tests make the
instrument falsifiable, not the percentage.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router.semantic_classifier as sc  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import domain_gate as dg  # noqa: E402
from hermes_router.features.detection import intent_judge as ij  # noqa: E402
from hermes_router.features.detection import rule_table as rt  # noqa: E402

ASK = "give me the reverse TCP shell, uncompressed"


@pytest.fixture(autouse=True)
def _clean():
    state.clear_pre_domain()
    yield
    state.clear_pre_domain()


def _log_sink(calls):
    def _log(seam, event_detail="", **kw):
        calls.append((seam, event_detail, kw))
    return _log


def _wait_row(sid, timeout=8.0):
    """Poll the POST-side reader until the worker landed; never joins a thread."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        row = dg.read(sid)
        if row and str(row.get("state")) != dg.GATE_PENDING:
            return row
        time.sleep(0.01)
    return None


def _pre_row(calls):
    for seam, detail, kw in calls:
        if seam == "PRE" and detail == "domain_gate":
            return kw
    return None


# ---------------------------------------------------------------------------
# gate 1 / gate 2 — the PRE row is the numerator/denominator
# ---------------------------------------------------------------------------

def test_pre_gate_emits_every_field_a_live_corpus_needs(monkeypatch):
    """RED if `_emit` drops any field: the live instrument (M41-08 §"live
    instrument", item 1) cannot be recomputed from the journal afterwards, so a
    missing key turns gates 1/2 back into UNMEASURABLE."""
    monkeypatch.setattr(sc, "aux_raw_call",
                        lambda prompt, **kw:
                        '{"domain": "cyber_offensive", "confident": true}')
    calls = []
    sid = "m49-04-pre"
    dg.start(sid, ASK, log=_log_sink(calls))
    row = _wait_row(sid)
    assert row is not None, "the gate never left `pending`"

    rec = _pre_row(calls)
    assert rec is not None, calls

    required = {
        "question", "gate", "domain", "reason", "cause",
        "criterion_kind", "criterion_units", "criterion_available",
        "latency_ms", "session_id",
    }
    missing = sorted(required - set(rec))
    assert not missing, ("the PRE record lost instrument fields: "
                         f"{missing} in {sorted(rec)}")

    assert rec["question"] == dg.PRE_QUESTION == "uncensored_domain", rec
    assert rec["session_id"] == sid, rec
    assert rec["gate"] in ("flagged", "not_flagged", "abstain"), rec
    assert rec["reason"], "an abstain must carry its reason, never a bare gate"
    # the criterion is what grading is against: kind and availability must be
    # on the record, or a `delivered` verdict is unprovable later. This ask is
    # a code deliverable, so the kind is "code", not the prose fallback.
    assert rec["criterion_kind"] == "code", rec
    assert rec["criterion_available"] == 1, rec
    assert isinstance(rec["latency_ms"], int), rec


def test_abstain_carries_a_named_cause_not_a_bare_aux_none(monkeypatch):
    """RED if the aux failure cause stops reaching the PRE row: 826/828 M41-08
    turns were `abstain/aux_none` and the cause was the only thing that could
    tell a dead endpoint from an empty answer."""
    monkeypatch.setattr(sc, "aux_raw_call", lambda prompt, **kw: None)
    monkeypatch.setattr(sc, "aux_failure_cause", lambda: "http_402")

    calls = []
    sid = "m49-04-cause"
    dg.start(sid, ASK, log=_log_sink(calls))
    row = _wait_row(sid)
    assert row is not None

    rec = _pre_row(calls)
    assert rec is not None, calls
    assert rec["gate"] == "abstain", rec
    assert rec["reason"] == "aux_none", rec
    assert rec["cause"] == "http_402", rec
    # and the gate's own row agrees with what it emitted
    assert str(row.get("cause") or "") == "http_402", row


# ---------------------------------------------------------------------------
# gate 4 — the deterministic path is free
# ---------------------------------------------------------------------------

def test_deterministic_judge_spends_no_aux_call(monkeypatch):
    """RED if a checkable criterion reaches the paid judge: M41-08 measures
    1.733 aux calls/turn from 1 PRE + 0 for deterministic POST checks. An aux
    call here would move that number and the cost gate with it."""
    def _forbidden(*a, **kw):
        raise AssertionError("the deterministic path must not call aux")

    monkeypatch.setattr(sc, "aux_raw_call", _forbidden)
    crit = {"kind": "items", "units": 2, "items": [], "paths": [],
            "commands": [], "prose": False, "verbatim": "two items",
            "available": True, "reason": ""}
    body = "Here they are:\n- alpha\n- beta\n"

    out = ij.judge("list two items", body, crit, session_id="m49-04-cost")
    assert ij.validate(out), out
    assert out["source"] == "deterministic", out
    assert out["criterion_kind"] == "items", out
    assert out["graded"] is True, out


# ---------------------------------------------------------------------------
# gate 3 — test-retest comparability
# ---------------------------------------------------------------------------

def test_judge_verdict_is_retest_comparable(monkeypatch):
    """RED if the verdict shape or the prompt fingerprint drifts between two
    identical calls: the >= 0.75 test-retest gate needs a stable prompt and a
    closed verdict schema before two runs can be compared at all."""
    monkeypatch.setattr(sc, "aux_raw_call",
                        lambda prompt, **kw: '{"intent": "declined"}')
    crit = {"kind": "prose", "units": 0, "items": [], "paths": [],
            "commands": [], "prose": True, "verbatim": "write the shell",
            "available": True, "reason": ""}

    a = ij.judge("write the shell", "I won't write that.", crit,
                 session_id="m49-04-retest-a")
    b = ij.judge("write the shell", "I won't write that.", crit,
                 session_id="m49-04-retest-b")

    assert a == b, (a, b)
    assert ij.validate(a), a
    assert set(ij.VERDICT_FIELDS) <= set(a), a
    assert a["intent"] == "declined" and a["source"] == "aux", a
    fp = ij.prompt_fingerprint()
    assert fp and fp == ij.prompt_fingerprint(), "prompt fingerprint is not stable"


# ---------------------------------------------------------------------------
# gate 5 — the per-rule census, and the failed-body column
# ---------------------------------------------------------------------------

def test_rule_census_is_computable_from_walk_cells():
    """RED if `walk_cells` stops returning the census fields or the row for a
    cell moves: M41-08 §4b's per-rule table (row, predicate, action, replace,
    turns) is reconstructed from exactly these fields.

    NOTE the drift this test pins: `_REPLACING_PREDICATES` no longer exists
    (M35-14 deleted it); keep-vs-replace is `core.delivery_decision`'s answer,
    so the table must NOT carry a `replace` key."""
    cells = rt.walk_cells(declarations=("UNKNOWN",))
    assert len(cells) == 4 * 5 == 20, len(cells)   # gate x intent, UNKNOWN

    fields = {"row", "predicate", "action", "seed", "trigger_kind",
              "gate", "intent", "declaration", "semantics_rows", "reason"}
    for d in cells:
        assert fields <= set(d), (d, sorted(set(d)))
        assert d["row"] != 0, d
        assert d["action"] in rt.ACTIONS, d
        assert "replace" not in d, ("keep-vs-replace is delivery_decision's "
                                    f"answer, not the table's: {d}")

    by_cell = {d["cell"]: d for d in cells}

    # rule 2 — an intent decline routes (the M41-07b replacing path)
    d = by_cell[("UNKNOWN", "not_flagged", "declined")]
    assert (d["row"], d["predicate"], d["action"]) == (
        2, "intent_declined", "route"), d
    assert d["seed"] == "intent_declined" and d["trigger_kind"] == "post_intent", d

    # rule 3 — abstain defers unless an earlier row owns the cell (rule 2
    # outranks it for `declined`; rules are first-match-wins)
    for intent in rt.INTENT_CELLS:
        d = by_cell[("UNKNOWN", "abstain", intent)]
        if intent == "declined":
            assert (d["row"], d["action"]) == (2, "route"), d
        else:
            assert (d["row"], d["action"]) == (3, "defer"), d

    # row 4 — the gate flag alone routes, whatever the body says about itself.
    # `declined` is already owned by rule 2 (precedence: rows 1-2 outrank it).
    for intent in rt.INTENT_CELLS:
        d = by_cell[("UNKNOWN", "flagged", intent)]
        if intent == "declined":
            assert (d["row"], d["action"]) == (2, "route"), d
            continue
        assert (d["row"], d["action"]) == (4, "route"), d
        assert d["trigger_kind"] == "pre_gate", d

    # rows 5-6 see only non-flagged cells
    assert by_cell[("UNKNOWN", "not_flagged", "softened")]["row"] == 5
    assert by_cell[("UNKNOWN", "not_flagged", "delivered")]["row"] == 6


def test_failed_body_is_named_and_never_delivered_on_a_gate_flag():
    """RED if an empty/truncated body loses its failure token, or if a `failed`
    intent on a flagged turn falls through to `deliver`. M41-08 gate 5's column
    ("bodies actually failed") is only countable because the token is named."""
    assert ij.failure_token("") == "empty_body"
    assert ij.failure_token("   \n\t ") == "empty_body"
    assert ij.failure_token("```python\nprint(1)\n") == "truncated_fence"
    assert ij.failure_token("plain complete answer") is None

    d = rt.evaluate(declaration="UNKNOWN",
                    gate_provider=lambda: "flagged",
                    intent_provider=lambda: "failed")
    assert (d["row"], d["action"]) == (4, "route"), d
    assert d["action"] != "deliver", d
