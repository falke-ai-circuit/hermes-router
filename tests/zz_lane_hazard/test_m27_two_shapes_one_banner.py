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


def test_parser_reads_both_token_shapes():
    """`N/M tok` and `tok N/M` must both yield tokens — B used the prefix form."""
    a = db._parse_decision_line(LINE_A)
    b = db._parse_decision_line(LINE_B)
    assert (a["ti"], a["to"]) == (2585, 94), a
    assert (b["ti"], b["to"]) == (1668, 83), "prefix shape dropped: %s" % b
    assert (b["r1"], b["r2"]) == (946, 946), b


def test_stacked_park_block_renders_one_banner_with_summed_numbers():
    """THE live path: the park slot hands the edge ONE block of three banners."""
    blocked = "\n\n".join([LINE_F, LINE_A, LINE_B])
    out = db.append_banner(BODY, "\n" + blocked, _knob_checked=True)

    lines = _decision_lines(out)
    assert len(lines) == 1, "one banner per turn, got %d:\n%s" % (len(lines), out)
    line = lines[0]
    assert re.search(r"x2\b", line), line
    assert "4253/177 tok" in line, "2585/94 + 1668/83 must SUM: %s" % line
    assert "$0.00018" in line, "0.00011 + 0.000070 must SUM: %s" % line
    assert re.search(r"rows 87-946", line), "row span must extend: %s" % line
    # the unrelated lane keeps its own line, untouched
    assert out.count("higher-self (frontier)") == 1, out


def test_verdict_shape_first_does_not_escape_the_merge():
    """Fire order is not guaranteed: B-then-A must also collapse to one line."""
    blocked = "\n\n".join([LINE_B, LINE_A])
    out = db.append_banner(BODY, "\n" + blocked, _knob_checked=True)
    lines = _decision_lines(out)
    assert len(lines) == 1, out
    assert "4253/177 tok" in lines[0], lines[0]
    assert re.search(r"x2\b", lines[0]), lines[0]


def test_two_separate_appends_still_merge_with_summable_tokens():
    """The non-stacked seam (two edges, two appends) keeps working."""
    out = db.append_banner(BODY, "\n" + LINE_A, _knob_checked=True)
    out = db.append_banner(out, "\n" + LINE_B, _knob_checked=True)
    lines = _decision_lines(out)
    assert len(lines) == 1, out
    assert "4253/177 tok" in lines[0], lines[0]
    assert "$0.00018" in lines[0], lines[0]
    assert re.search(r"rows 87-946", lines[0]), lines[0]


def test_unparseable_block_member_is_appended_never_dropped():
    """Fail-open survives the per-line walk: an unreadable decision line stays."""
    weird = "%s impulse (decision) | nothing the parser knows ·" % db.BANNER_HEAD
    out = db.append_banner(BODY, "\n" + "\n\n".join([LINE_A, weird]), _knob_checked=True)
    assert out.count(HEAD) == 2, "the unreadable banner must not be dropped: %s" % out
    assert "2585/94 tok" in out, out
