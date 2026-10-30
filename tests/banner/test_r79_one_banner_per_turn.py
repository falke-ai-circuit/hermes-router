"""R79 — ONE decision banner per turn: a second decision line MERGES at the edge.

The lane had two renderers producing two shapes for the same lane, and both
lines reached the delivery edge. The fix does not reroute either renderer: at
the append boundary a second decision-lane line is merged into the one already
delivered (counts, tokens and cost summed; row span extended), and nothing is
appended. Other lanes are never touched.
"""
from __future__ import annotations

import re

import pytest

from hermes_router import debug_banner as db
from hermes_router import reflex_lane as rl


def _lane_state():
    """Every module-level store this lane keys by session id."""
    stores = [getattr(rl, "_ACC", None)]
    for name in ("_ANCHOR_BANNERS", "_CONSUMED_DECISION", "_DECISION_HOLDS"):
        stores.append(getattr(db, name, None))
    return [s for s in stores if isinstance(s, dict)]


@pytest.fixture(autouse=True)
def _isolate_lane_state():
    """Test-isolation defect guard, not a product guard.

    This file exercises the delivery edge, which reads module-level stores keyed
    by session id. Clear them on the way IN (a leftover from another file must
    not decide this file's result) and again on the way OUT (our entries must
    never reach rider16/rider18, which assert on those same slots).
    """
    for store in _lane_state():
        store.clear()
    yield
    for store in _lane_state():
        store.clear()

HEAD = "· router · impulse (decision)"


def _line(n, leads, holds, ti, to, cost, r1, r2, star=False):
    head = "%s impulse (decision)" % db.BANNER_HEAD
    if n > 1:
        head += " x%d" % n
    classes = []
    if leads:
        classes.append("%d lead" % leads)
    if holds:
        classes.append("%d hold" % holds)
    cost_s = ("$%.5f" % cost) if cost > 0 else ("$0.00000*" if star or ti else "$0.00000")
    row = ("rows %d-%d" % (r1, r2)) if r2 > r1 else ("row %d" % r1)
    return " | ".join([head, " · ".join(classes), "jev @ typesafe",
                       "%d/%d tok" % (ti, to), cost_s, row])


def test_two_decision_lines_merge_into_one_with_summed_numbers():
    body = "the delivered answer body"
    first = _line(1, 0, 1, 100, 10, 0.0, 466, 466, star=True)
    second = _line(1, 1, 0, 200, 20, 0.0, 471, 471, star=True)
    body = db.append_banner(body, "\n" + first, _knob_checked=True)
    body = db.append_banner(body, "\n" + second, _knob_checked=True)
    assert body.count(HEAD) == 1, body
    line = next(ln for ln in body.splitlines() if HEAD in ln)
    assert re.search(r"x2\b", line), line
    assert "1 lead · 1 hold" in line, line
    assert "300/30 tok" in line, line
    assert re.search(r"rows 466-471", line), line


def test_hold_only_says_hold_and_never_zero_lead():
    body = db.append_banner("answer", "\n" + _line(1, 0, 1, 90, 9, 0.0, 12, 12), _knob_checked=True)
    line = next(ln for ln in body.splitlines() if HEAD in ln)
    assert "hold" in line and "0 lead" not in line, line


def test_other_lanes_are_never_merged():
    anchor = "· router · anchor (frontier) | trigger: open_question | row 9"
    body = db.append_banner("answer", "\n" + _line(1, 1, 0, 10, 2, 0.0, 5, 5), _knob_checked=True)
    body = db.append_banner(body, "\n" + anchor, _knob_checked=True)
    assert body.count(HEAD) == 1, body
    assert anchor in body, "an anchor banner must keep its own line"
    body2 = db.append_banner(body, "\n" + _line(1, 0, 1, 10, 2, 0.0, 6, 6), _knob_checked=True)
    assert body2.count(HEAD) == 1, body2
    assert anchor in body2


def test_unparseable_decision_line_appends_instead_of_being_lost():
    body = db.append_banner("answer", "\n" + _line(1, 1, 0, 10, 2, 0.0, 5, 5), _knob_checked=True)
    weird = "%s impulse (decision) | something new the parser has never seen ·" % db.BANNER_HEAD
    body2 = db.append_banner(body, "\n" + weird, _knob_checked=True)
    assert body2.count(HEAD) == 2, "fail-open: the banner must not be dropped"
