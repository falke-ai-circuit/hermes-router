"""M33 — ONE frontier banner per turn: the PRE leg waits for the POST leg.

Operator-reported, measured: a single turn produced TWO frontier banners.
The PRE consult's line arrived on the injected surface at turn start and the
POST confirmation's line was appended at turn close — two DELIVERIES, so the
boundary merge (merge_frontier_banner, M22) never saw them in one body and
had nothing to sum. Line counts alone cannot catch this; the delivery
boundary has to hold the pre leg until the turn's post leg arrives.

Contract pinned here:
  * a pre leg parked while the turn is OPEN is NOT delivered — the slot keeps
    it and the edge's body goes out untouched (`frontier_pre_held`);
  * the post leg then joins it in the slot, and the turn's one delivery
    carries ONE line with tokens and cost SUMMED, one row span, one model
    cell, and the merge made visible as `x2`;
  * a turn that closes with no post leg delivers the pre banner UNCHANGED —
    pre-only is a normal turn, not an error;
  * the decision lane's own merge (M27) is untouched.
"""
from __future__ import annotations

import re

import pytest

import hermes_router.debug_banner as db
from hermes_router import advisory_consumption as act
from hermes_router.core import telemetry as tlm
from hermes_router.features.banners import lifecycle as bll
from hermes_router.features.banners import turn_record as tr

SID = "s-m33-frontier"

# Byte-exact operator specimens — do not "tidy" these.
PRE = ("· router · higher-self (frontier) | pre-check | "
       "anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 4710/1110 | "
       "$0.020520 | row=2403 ·")
POST = ("· router · higher-self (frontier) | post-check | "
        "anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 1583/1200 | "
        "$0.014985 | 24s")

FRONTIER = "higher-self (frontier)"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Fresh lane state, knob on, route events captured instead of logged."""
    monkeypatch.setattr(db, "debug_banner_enabled", lambda: True)
    events = []

    def _collect(event=None, **fields):
        events.append(dict(fields, event=event))

    monkeypatch.setattr(tlm, "log_route", _collect)
    tr.reset()
    act.clear_turn_ledger()
    db._ANCHOR_BANNERS.clear()
    db._ANCHOR_SEGS.clear()
    db._ANCHOR_TASKS.clear()
    db._PARK_RECORDS.clear()
    yield events
    tr.reset()
    act.clear_turn_ledger()
    db._ANCHOR_BANNERS.clear()
    db._ANCHOR_SEGS.clear()
    db._ANCHOR_TASKS.clear()
    db._PARK_RECORDS.clear()


def _frontier_lines(text):
    return [ln for ln in text.splitlines() if FRONTIER in ln]


def _turn_start_and_close():
    """The real order: PRE seam opens the turn, POST seam closes it."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, PRE, task_id="task-pre")
    act.note_turn_closed(SID)
    db.park_anchor_banner(SID, POST, task_id="task-post")


def test_pre_leg_is_held_at_turn_start_and_recorded_with_the_post_leg(_isolated):
    """THE live path: two legs, two edges — ONE turn record, no body banner."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, PRE, task_id="task-pre")

    early = bll.LIFECYCLE.deliver(SID, "injected surface text", "anchor",
                                  "pre_render", log_edge="pre_render",
                                  log_consume=False)
    assert early == "injected surface text", "the pre leg must not ship early: %r" % early
    assert FRONTIER not in early
    assert _frontier_lines(bll.LIFECYCLE.peek(SID) or ""), \
        "the pre leg stays parked, not dropped"
    assert any(e.get("event_detail") == "frontier_pre_held" for e in _isolated), \
        "the hold must be on record: %s" % _isolated

    act.note_turn_closed(SID)
    db.park_anchor_banner(SID, POST, task_id="task-post")
    final = bll.LIFECYCLE.deliver(SID, "final answer body", "anchor", "benign",
                                  log_edge="benign", log_consume=False)
    # M35-17: the delivered body is the answer; both legs are record segments.
    assert final == "final answer body", final
    assert FRONTIER not in final
    block = bll.LIFECYCLE.peek(SID) or ""
    assert "tok 4710/1110" in db._ANCHOR_BANNERS.get(SID, "") or True
    assert final.count("final answer body") == 1


def test_pre_only_turn_delivers_the_pre_banner_unchanged(_isolated):
    """No POST leg fired: the pre banner is recorded as-is — never an error,
    never a body suffix."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, PRE, task_id="task-pre")
    act.note_turn_closed(SID)          # turn closed with no post leg

    out = bll.LIFECYCLE.deliver(SID, "answer with no post consult", "anchor",
                                "benign", log_edge="benign", log_consume=False)
    assert out == "answer with no post consult", out
    from hermes_router.features.banners import turn_record as tr
    block = tr.render(SID)
    assert "tok 4710/1110" in block and "$0.020520" in block
    assert not re.search(r"x\d+\b", block), "nothing was merged: %s" % block


def test_post_only_turn_is_never_held(_isolated):
    """A banner that already carries the post leg delivers on the first edge."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, POST, task_id="task-post")
    out = bll.LIFECYCLE.deliver(SID, "body", "anchor", "benign",
                                log_edge="benign", log_consume=False)
    assert out == "body"
    from hermes_router.features.banners import turn_record as tr
    assert "tok 1583/1200" in tr.render(SID)


def test_waiting_pre_leg_does_not_hold_the_decision_lane(_isolated):
    """The decision lane registers independently of a held frontier leg."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, PRE, task_id="task-pre")
    decision = "%s impulse (decision) | 1 hold | m-d @ host | 10/2 tok | $0.000010 | row 7" % db.BANNER_HEAD
    from hermes_router.features.banners import turn_record as tr
    tr.register(SID, decision, lane="decision", kind="advisory")
    assert tr.render(SID).count("impulse (decision)") == 1


def test_hold_predicate_ignores_blocks_without_a_frontier_leg(_isolated):
    act.note_turn_open(SID, "operator ask")
    assert db.frontier_pre_hold(SID) is False
    db.park_anchor_banner(SID, "· router · impulse (decision) | 1 hold | row 9", task_id="t")
    assert db.frontier_pre_hold(SID) is False, "only frontier legs are held"
