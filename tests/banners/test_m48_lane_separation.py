"""M48: each LANE carries its OWN banner line, with its OWN figures.

Owner ruling (2026-11-07): one line per lane — decision, frontier, uncensored —
and a lane that fired more than once aggregates inside its own line (×N), never
across lanes.

The defect this pins: `compose_line` classified a whole REGISTERED SEGMENT with
`_group_of(lane, text)`, which substring-scans the text and tests
'decision'/'reflex'/'impulse' BEFORE 'frontier'. One segment can legitimately
carry several lane lines (park_task_banner composes a task's legs; the decision
rollup and the frontier PRE leg can share the anchor slot), so a mixed segment
collapsed into the 'decision' bucket. `_merge` then joined the models with '/'
and SUMMED the costs — a frontier consult's tokens and dollars rendered on the
decision line. Live specimen (shadow, api_1794045378_a20b632d):

  · router · impulse (decision) | 1 lead+pre-check | ×2 |
    jev @ typesafe/anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 |
    tok 1751/1022 | $0.0138

...for ONE billed consult of $0.013722 plus two sub-cent Jev receipts. Summing
the delivered lines double-counted the consult; the decision lane's own spend
(~$0.0001) appeared nowhere.
"""
from __future__ import annotations

import pytest

from features.banners import turn_record as tr


DECISION = ("· router · impulse (decision) | 1 lead | tok 2714/86 | $0.000114 "
            "| provider=typesafe | initiator=agent")
FRONTIER = ("· router · higher-self (frontier) | pre-check | "
            "anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 1751/1022 "
            "| $0.013722")
UNCENSORED = ("· router · shadow-self (uncensored) | render | "
              "abliterated-large-v2 @ venice | tok 900/400 | $0.003000")


@pytest.fixture()
def session():
    try:
        tr._RECORDS.clear()
    except Exception:
        pass
    return "test-m48-lane-separation"


def _lines(out: str):
    return [ln for ln in str(out or "").splitlines() if ln.strip()]


def _lane_of(line: str) -> str:
    low = line.lower()
    if "higher-self" in low or "frontier" in low:
        return "frontier"
    if "shadow-self" in low or "uncensored" in low:
        return "uncensored"
    if "impulse" in low or "decision" in low or "reflex" in low:
        return "decision"
    return "?"


def test_mixed_segment_yields_one_line_per_lane(session):
    """A segment carrying two lane lines must render as two lines, not one."""
    # One registered fragment holding BOTH lanes — the live shape.
    tr.register(session, "%s\n%s" % (DECISION, FRONTIER), kind="anchor")
    lines = _lines(tr.compose_line(session))
    assert len(lines) == 2, lines
    assert [_lane_of(ln) for ln in lines] == ["decision", "frontier"]


def test_frontier_figures_never_land_on_the_decision_line(session):
    tr.register(session, "%s\n%s" % (DECISION, FRONTIER), kind="anchor")
    by_lane = {_lane_of(ln): ln for ln in _lines(tr.compose_line(session))}
    decision, frontier = by_lane["decision"], by_lane["frontier"]

    # The decision line keeps its own numbers and its own provider only.
    assert "2714/86" in decision
    assert "1751" not in decision, decision
    assert "typesafe" in decision
    assert "claude-sonnet-5.5" not in decision, decision
    assert "127.0.0.1:8680" not in decision, decision

    # ...and the frontier line keeps its own.
    assert "1751/1022" in frontier
    assert "2714" not in frontier, frontier
    assert "claude-sonnet-5.5" in frontier
    assert "typesafe" not in frontier, frontier


def test_no_cross_lane_model_join(session):
    """`/`-joined models were the visible symptom of the collapse."""
    tr.register(session, "%s\n%s" % (DECISION, FRONTIER), kind="anchor")
    for ln in _lines(tr.compose_line(session)):
        assert "jev @ typesafe/anthropic" not in ln, ln


def test_cost_is_not_double_counted_across_lines(session):
    """Summing the delivered lines must not exceed the true spend per lane."""
    tr.register(session, "%s\n%s" % (DECISION, FRONTIER), kind="anchor")
    by_lane = {_lane_of(ln): ln for ln in _lines(tr.compose_line(session))}
    # decision renders its own sub-cent figure; frontier carries the consult.
    assert "$0.0001" in by_lane["decision"], by_lane["decision"]
    assert "$0.0137" in by_lane["frontier"], by_lane["frontier"]


def test_three_lanes_three_lines(session):
    tr.register(session, "%s\n%s" % (DECISION, FRONTIER), kind="anchor")
    tr.register(session, UNCENSORED, kind="anchor")
    lines = _lines(tr.compose_line(session))
    assert len(lines) == 3, lines
    assert sorted(_lane_of(ln) for ln in lines) == [
        "decision", "frontier", "uncensored"]


def test_repeats_aggregate_inside_their_own_lane(session):
    """Two decision verdicts fold into ONE decision line carrying ×2."""
    second = ("· router · impulse (decision) | 1 lead | tok 2416/86 "
              "| $0.000101 | provider=typesafe | initiator=agent")
    tr.register(session, "%s\n%s" % (DECISION, second), kind="anchor")
    lines = _lines(tr.compose_line(session))
    assert len(lines) == 1, lines
    assert "×2" in lines[0], lines[0]
    # summed inside the lane: 2714+2416 in, 86+86 out
    assert "5130/172" in lines[0], lines[0]


def test_identical_leg_registered_twice_is_not_double_summed(session):
    """Same measurement registered at park AND at the delivery edge."""
    tr.register(session, FRONTIER, kind="anchor")
    tr.register(session, FRONTIER, kind="anchor")
    lines = _lines(tr.compose_line(session))
    assert len(lines) == 1, lines
    assert "1751/1022" in lines[0], lines[0]
