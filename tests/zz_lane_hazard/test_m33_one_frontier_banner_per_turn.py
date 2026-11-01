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


def test_pre_leg_is_held_at_turn_start_and_merged_with_the_post_leg(_isolated):
    """THE live path: two legs, two edges, ONE delivered frontier banner."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, PRE, task_id="task-pre")

    # Turn start: the injected surface delivers. The pre leg may not ride it.
    early = bll.LIFECYCLE.deliver(SID, "injected surface text", "anchor",
                                  "pre_render", log_edge="pre_render",
                                  log_consume=False)
    assert early == "injected surface text", "the pre leg must not ship early: %r" % early
    assert FRONTIER not in early
    assert _frontier_lines(bll.LIFECYCLE.peek(SID) or ""), \
        "the pre leg stays parked, not dropped"
    assert any(e.get("event_detail") == "frontier_pre_held" for e in _isolated), \
        "the hold must be on record: %s" % _isolated

    # Turn close: the post leg parks, the turn's one delivery carries the merge.
    act.note_turn_closed(SID)
    db.park_anchor_banner(SID, POST, task_id="task-post")
    final = bll.LIFECYCLE.deliver(SID, "final answer body", "anchor", "benign",
                                  log_edge="benign", log_consume=False)

    lines = _frontier_lines(final)
    assert len(lines) == 1, "one frontier banner per turn, got %d:\n%s" % (
        len(lines), final)
    line = lines[0]
    assert re.search(r"x2\b", line), "the merge must be visible: %s" % line
    assert "tok 6293/2310" in line, "4710+1583 / 1110+1200 must SUM: %s" % line
    assert "$0.035505" in line, "0.020520+0.014985 must SUM: %s" % line
    assert "row=2403" in line, "one row span: %s" % line
    assert line.count("@ 127.0.0.1:8680") == 1, "one model cell: %s" % line
    assert "24s" in line, "latency summed: %s" % line
    assert final.count("final answer body") == 1


def test_pre_only_turn_delivers_the_pre_banner_unchanged(_isolated):
    """No POST leg fired: the pre banner ships as-is — never an error."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, PRE, task_id="task-pre")
    act.note_turn_closed(SID)          # turn closed with no post leg

    out = bll.LIFECYCLE.deliver(SID, "answer with no post consult", "anchor",
                                "benign", log_edge="benign", log_consume=False)
    lines = _frontier_lines(out)
    assert len(lines) == 1, out
    line = lines[0]
    assert PRE.rstrip() in out or line == PRE.rstrip(), \
        "the pre banner must arrive unchanged: %s" % line
    assert "tok 4710/1110" in line and "$0.020520" in line
    assert not re.search(r"x\d+\b", line), "nothing was merged: %s" % line


def test_post_only_turn_is_never_held(_isolated):
    """A banner that already carries the post leg delivers on the first edge."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, POST, task_id="task-post")
    out = bll.LIFECYCLE.deliver(SID, "body", "anchor", "benign",
                                log_edge="benign", log_consume=False)
    assert len(_frontier_lines(out)) == 1, out
    assert "tok 1583/1200" in out


def test_waiting_pre_leg_does_not_hold_the_decision_lane(_isolated):
    """M27 is untouched: a decision banner still merges into its own line."""
    act.note_turn_open(SID, "operator ask")
    db.park_anchor_banner(SID, PRE, task_id="task-pre")
    decision = "%s impulse (decision) | 1 hold | m-d @ host | 10/2 tok | $0.000010 | row 7" % db.BANNER_HEAD
    out = db.append_banner("body", "\n" + decision, _knob_checked=True)
    assert out.count("impulse (decision)") == 1, out
    merged = db.append_banner(
        out, "\n" + decision.replace("10/2 tok", "5/1 tok"), _knob_checked=True)
    assert merged.count("impulse (decision)") == 1, merged
    assert "15/3 tok" in merged, merged
    # the frontier leg is still parked and untouched by the decision merge
    assert _frontier_lines(bll.LIFECYCLE.peek(SID) or "")


def test_hold_predicate_ignores_blocks_without_a_frontier_leg(_isolated):
    act.note_turn_open(SID, "operator ask")
    assert db.frontier_pre_hold(SID) is False
    db.park_anchor_banner(SID, "· router · impulse (decision) | 1 hold | row 9", task_id="t")
    assert db.frontier_pre_hold(SID) is False, "only frontier legs are held"
