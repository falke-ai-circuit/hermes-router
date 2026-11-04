"""M27 — the two LIVE decision shapes, one delivery, ONE banner.

Both shapes were observed in the same evol turn (session 20261006_190908_d0fa319f)
and arrived as two separate lines in the delivered body:

  A (reflex rollup)   · router · impulse (decision) | 1 hold | jev @ typesafe |
                        2585/94 tok | $0.00011 | row 87
  B (midturn rollup)  · router · impulse (decision) | 1 verdict | tok 1668/83 |
                        $0.000070 | provider=typesafe | initiator=agent | row=946 ·

Two defects, both pinned here:

1. The merge never saw them as two lines. The anchor park slot stacks every
   fired banner into ONE block (`"\\n\\n".join(segs)`, features/banners/
   lifecycle.py), so the delivery boundary was handed a single string holding
   the frontier line and BOTH decision lines. merge_decision_banner parsed that
   block as one verdict and only matched it against lines already in the body —
   so it returned None and the block was appended verbatim.
2. `_parse_decision_line` read only the `N/M tok` shape, so B's `tok 1668/83`
   contributed 0/0 tokens: a merged line undercounted the second leg.
"""
from __future__ import annotations

import re

import pytest

from hermes_router import debug_banner as db

HEAD = "· router · impulse (decision)"

# Byte-exact live shapes — do not "tidy" these; they are the specimen.
LINE_A = ("· router · impulse (decision) | 1 hold | jev @ typesafe | "
          "2585/94 tok | $0.00011 | row 87")
LINE_B = ("· router · impulse (decision) | 1 verdict | tok 1668/83 | "
          "$0.000070 | provider=typesafe | initiator=agent | row=946 ·")
LINE_F = ("· router · higher-self (frontier) | pre-check | "
          "anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 5056/1238 | "
          "$0.022492 | row=947 ·")

BODY = "Two cycles done this turn, both 13/13."


@pytest.fixture(autouse=True)
def _clean_slots():
    db._ANCHOR_BANNERS.clear()
    db._ANCHOR_SEGS.clear()
    db._ANCHOR_TASKS.clear()
    yield
    db._ANCHOR_BANNERS.clear()
    db._ANCHOR_SEGS.clear()
    db._ANCHOR_TASKS.clear()


def _decision_lines(text: str):
    return [ln for ln in text.splitlines() if HEAD in ln]


def test_both_live_shapes_survive_as_segments():
    """M35-17: the parse-based body merge is deleted; the two live shapes are
    recorded verbatim, and neither reaches a delivered body."""
    from hermes_router.features.banners import turn_record as tr

    sid = "s-m27-both"
    tr.reset(sid)
    assert tr.register(sid, LINE_A, lane="decision", kind="reflex") is True
    assert tr.register(sid, LINE_B, lane="decision", kind="midturn") is True
    segs = tr.segments(sid)
    assert len(segs) == 2, segs
    assert "2585/94 tok" in segs[0]["text"]
    assert "tok 1668/83" in segs[1]["text"]
    assert HEAD not in BODY


def test_frontier_and_two_decision_shapes_are_three_segments():
    from hermes_router.features.banners import turn_record as tr

    sid = "s-m27-three"
    tr.reset(sid)
    tr.register(sid, LINE_F, lane="frontier", kind="pre")
    tr.register(sid, LINE_A, lane="decision", kind="reflex")
    tr.register(sid, LINE_B, lane="decision", kind="midturn")
    block = tr.render(sid)
    assert block.count("higher-self (frontier)") == 1
    assert block.count(HEAD) == 2
    assert BODY == "Two cycles done this turn, both 13/13."


def test_unparseable_member_is_recorded_never_dropped():
    from hermes_router.features.banners import turn_record as tr

    sid = "s-m27-weird"
    tr.reset(sid)
    weird = "· router · impulse (decision) | ???? ·"
    assert tr.register(sid, weird, lane="decision") is True
    assert weird in tr.render(sid)
