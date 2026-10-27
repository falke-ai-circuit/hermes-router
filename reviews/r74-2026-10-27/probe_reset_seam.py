"""R74 review probe — the reset seam, tested OUTSIDE the suite.

Spec §3.4: clear the accumulator when the parked banner is consumed
("a stale reset must never clear a live turn's counts"), so that "a second
turn does not inherit the first turn's counts".

The implementation does not clear at the consume seam. It keys on the park
slot being EMPTY at the next fire. These probes test what that means in the
cases the shipped pins do not cover.

Run: PYTHONPATH=/opt/data/plugins python -m pytest reviews/r74-2026-10-27/probe_reset_seam.py -q
"""
import os
import sys

import pytest

from hermes_router import advisory_consumption as act
from hermes_router import debug_banner as db
from hermes_router import reflex_lane as rl

SID = "r74-probe"

PARSED = {"options": [{"p": 0.61, "label": "run the check", "kind": "test",
                       "move": "run_check", "target": ""},
                      {"p": 0.44, "label": "read the file", "kind": "read",
                       "move": "read_file", "target": ""}],
          "margin": 0.17}


def _usage(ti, to):
    return {"usage": {"prompt_tokens": ti, "completion_tokens": to}}


@pytest.fixture(autouse=True)
def clean():
    rl.clear(SID)
    act.clear_turn_ledger(SID)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(SID, None)
    yield
    rl.clear(SID)
    act.clear_turn_ledger(SID)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(SID, None)


def _lines(text):
    return [ln for ln in str(text or "").splitlines() if db.BANNER_HEAD in ln]


def test_P1_two_turns_no_delivery_between_them():
    """Turn 1 parks and is NEVER consumed (dropped/aborted turn); turn 2 fires.

    The slot still holds turn 1's text, so _slot_live() is True and the
    accumulator is NOT rolled: turn 2 should show x1 / its own tokens.
    """
    act.note_turn_open(SID, "turn one question")
    rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    first = db._ANCHOR_BANNERS.get(SID, "")
    assert "x2" in first and "tok 2000/200" in first, first

    act.note_turn_open(SID, "a different turn two question")
    banner = rl._receipt(SID, PARSED, _usage(7, 3), "every_call")
    text = db._ANCHOR_BANNERS.get(SID, "")
    print("\nP1 turn-2 rendered: %r" % text)
    assert "tok 7/3" in text, "turn 2 inherited turn 1's counts: %r" % text
    assert "x2" not in text and "x3" not in text, "turn 2 inherited turn 1's fire count: %r" % text


def test_P2_turn_no_unknown_second_fire_after_consume():
    """No turn ledger at all (turn_no == 0) — SOURCE-gated: whose sessions have 0?"""
    rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    db.consume_parked_banner(SID)
    banner = rl._receipt(SID, PARSED, _usage(7, 3), "every_call")
    text = db._ANCHOR_BANNERS.get(SID, "")
    print("\nP2 post-consume (turn_no=0) re-park: %r" % text)
    assert not text, ("a fire after the delivery edge re-parked a banner with turn_no unknown: %r"
                      % text)


def test_P3_other_sessions_consume_does_not_touch_live_counts():
    other = "r74-probe-other"
    try:
        rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
        rl._receipt(SID, PARSED, _usage(500, 50), "every_call")
        db.consume_parked_banner(other)          # wrong session
        text = db._ANCHOR_BANNERS.get(SID, "")
        assert "tok 1500/150" in text, "another session's consume cleared live counts: %r" % text
    finally:
        rl.clear(other)
        act.clear_turn_ledger(other)


def test_P4_stale_reset_cannot_clear_a_live_turn():
    """A consume that happened BEFORE this turn's first fire must not erase it."""
    act.note_turn_open(SID, "q1")
    rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    db.consume_parked_banner(SID)               # turn 1 delivered
    act.note_turn_open(SID, "q2")
    rl._receipt(SID, PARSED, _usage(9, 1), "every_call")
    rl._receipt(SID, PARSED, _usage(9, 1), "every_call")
    text = db._ANCHOR_BANNERS.get(SID, "")
    print("\nP4 turn-2 two fires: %r" % text)
    assert "tok 18/2" in text and "x2" in text, text


def test_P5_cap_and_row_suffix_under_long_triggers():
    """MAX_BANNER_CHARS is applied around the xN marker and the row suffix."""
    long_seam = "every_call_" + "x" * 200
    for i in range(3):
        rl._receipt(SID, PARSED, _usage(1000, 100), long_seam)
    text = db._ANCHOR_BANNERS.get(SID, "")
    print("\nP5 long-trigger parked: len=%d tail=%r" % (len(text), text[-30:]))
    assert text, "a long trigger must not drop the whole turn's banner"
    assert len(text) <= db.MAX_BANNER_CHARS, "parked segment exceeds cap: %d" % len(text)
    assert "row=reflex." in text, "row suffix lost: %r" % text
    assert "x3" in text, "fire count lost: %r" % text
    assert "tok 3000/300" in text, "sums lost: %r" % text


def test_P6_two_seams_long_triggers_stay_under_cap():
    s1 = "every_call_" + "x" * 300
    s2 = "midturn_" + "y" * 300
    rl._receipt(SID, PARSED, _usage(1000, 100), s1)
    rl._receipt(SID, PARSED, _usage(1000, 100), s2)
    text = db._ANCHOR_BANNERS.get(SID, "")
    print("\nP6 parked len=%d: %r" % (len(text), text))
    assert len(text) <= db.MAX_BANNER_CHARS, len(text)
    assert "row=reflex.1-2" in text, text
    assert "x2" in text and "tok 2000/200" in text, text


def test_P7_fail_open_render_and_accumulator(monkeypatch):
    """Any accumulator/render exception must yield '' and never raise."""
    calls = []
    orig = rl._park_summary
    rl._park_summary = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert rl._receipt(SID, PARSED, _usage(1000, 100), "every_call") == ""
        calls.append("render_raise_swallowed")
    finally:
        rl._park_summary = orig

    orig2 = rl._acc_update
    rl._acc_update = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert rl._receipt(SID, PARSED, _usage(1000, 100), "every_call") == ""
        calls.append("acc_raise_swallowed")
    finally:
        rl._acc_update = orig2
    print("\nP7 %s" % calls)
    assert calls == ["render_raise_swallowed", "acc_raise_swallowed"]
