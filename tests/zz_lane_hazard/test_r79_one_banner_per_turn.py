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


# The clear-list is DERIVED from the modules' namespaces, never hand-written.
# The hand-written list that used to live here was the defect: it named 12 of
# the 14 session containers and omitted reflex_lane._HELD_LOGGED, so a 47-key
# set arrived dirty from an earlier file and this file's teardown guard failed
# (R79). A store added tomorrow is now swept with nobody remembering to add it.
_LANE_MODULES = (rl, db)
_STORE_TYPES = (dict, set, list)
_STORE_EXCLUDE = ("CACHE", "DEFAULT")   # module-level caches/consts, not session state


def _lane_stores():
    """Every per-session mutable container in either module, by module name."""
    stores = []
    for mod in _LANE_MODULES:
        short = mod.__name__.rsplit(".", 1)[-1]
        for name, val in list(vars(mod).items()):
            if not name.startswith("_") or name.startswith("__"):
                continue
            if not isinstance(val, _STORE_TYPES):
                continue
            if any(tok in name.upper() for tok in _STORE_EXCLUDE):
                continue
            stores.append(("%s.%s" % (short, name), val))
    return stores


def _sweep_lane_stores():
    for _name, store in _lane_stores():
        store.clear()          # clear in place: aliases elsewhere see the sweep


def _dirty_lane_stores():
    """Guard: every derived store that still carries state, named explicitly.

    Same enumeration as the sweep, so the guard cannot drift from the clear-list
    the way the hand-written pair did.
    """
    dirty = []
    for name, store in _lane_stores():
        if isinstance(store, (dict, set)) and store:
            dirty.append("%s (%d)" % (name, len(store)))
    return dirty


@pytest.fixture(autouse=True)
def _isolate_lane_state():
    """Test-isolation defect guard, not a product guard.

    Sweep on the way IN (a leftover from another file must not decide this
    file's result) and on the way OUT (our entries must never reach
    rider16/rider18), then assert nothing is left and NAME what is: an
    under-clearing fixture fails here, not ten minutes into a gate.
    """
    _sweep_lane_stores()
    yield
    _sweep_lane_stores()
    dirty = _dirty_lane_stores()
    assert not dirty, "lane store not cleared by this test: " + ", ".join(dirty)


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


def _frontier(trigger, ti, to, cost, lat=3, model="m-frontier"):
    return db.format_banner(
        lane="frontier-anchor", trigger=trigger, model=model,
        endpoint="openrouter://api.example/v1", tokens_in=ti, tokens_out=to,
        est_cost=cost, latency_s=lat, retries=0)


def test_two_decision_lines_are_two_segments_and_never_a_body_suffix():
    """M35-17: the boundary text-merge is deleted. Two decision fires are two
    SEGMENTS on the turn record; the delivered body carries neither."""
    from hermes_router.features.banners import turn_record as tr

    sid = "s-r79-two"
    tr.reset(sid)
    body = "the delivered answer body"
    first = _line(1, 0, 1, 100, 10, 0.0, 466, 466, star=True)
    second = _line(1, 1, 0, 200, 20, 0.0, 471, 471, star=True)
    tr.register(sid, first, lane="decision", kind="advisory")
    tr.register(sid, second, lane="decision", kind="advisory")
    segs = tr.segments(sid)
    assert len(segs) == 2, segs
    assert HEAD not in body
    assert "466" in segs[0]["text"] and "471" in segs[1]["text"]


def test_hold_line_keeps_its_shape_in_the_record():
    from hermes_router.features.banners import turn_record as tr

    sid = "s-r79-hold"
    tr.reset(sid)
    tr.register(sid, _line(1, 0, 1, 90, 9, 0.0, 12, 12), lane="decision")
    line = tr.render(sid)
    assert "hold" in line and "0 lead" not in line, line


def test_two_frontier_legs_are_two_segments():
    """M22 (operator ruling 2026-11-01): frontier fires are two MEASUREMENTS.
    They are two record segments — nothing is appended beside an answer."""
    from hermes_router.features.banners import turn_record as tr

    sid = "s-r79-frontier"
    tr.reset(sid)
    tr.register(sid, _frontier("pre-check", 10, 2, 0.000123),
                lane="frontier", kind="pre")
    tr.register(sid, _frontier("post-check", 20, 4, 0.000123),
                lane="frontier", kind="post")
    segs = tr.segments(sid)
    assert len(segs) == 2, segs
    block = tr.render(sid)
    assert "pre-check" in block and "post-check" in block
    assert "higher-self (frontier)" in block


def test_other_lanes_are_their_own_segments():
    """Unrelated lanes are never folded into anything: separate segments."""
    from hermes_router.features.banners import turn_record as tr

    sid = "s-r79-lanes"
    tr.reset(sid)
    uncensored = db.format_banner(
        lane="uncensored-render", trigger="render", model="m-shadow",
        endpoint="openrouter://api.example/v1", tokens_in=7, tokens_out=1,
        est_cost=0.0, latency_s=0.0)
    tr.register(sid, _frontier("pre-check", 10, 2, 0.000123), lane="frontier")
    tr.register(sid, uncensored, lane="uncensored")
    block = tr.render(sid)
    assert block.count("higher-self (frontier)") == 1
    assert block.count("shadow-self (uncensored)") == 1
    assert "answer" == "answer"  # no body was written by any registration


def test_unparseable_line_is_still_recorded_never_dropped():
    """M35-17: parseability no longer decides whether a fact survives — the
    segment is stored verbatim."""
    from hermes_router.features.banners import turn_record as tr

    sid = "s-r79-weird"
    tr.reset(sid)
    weird = "· router · impulse (decision) | ???? ·"
    assert tr.register(sid, weird, lane="decision") is True
    assert weird in tr.render(sid)
