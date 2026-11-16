"""M50-26 — BANNER-SPEC §1: ONE canonical head per delivered body.

Live specimen (analyst session api_1794774302_b2dd3a43, render record
2026-11-15T20:27:15Z, stage=BENIGN_BANNER, 4031 chars): the body carried TWO
canonical heads because `turn_record.compose_line` prefixed EVERY per-lane line
with the same head:

  · router · impulse (decision) | 1 verdict+1 hold | ×2 | row=1032 ·/jev @ typesafe | tok 1647/74 | $0.0002 | provider=typesafe | initiator=agent
  · router · higher-self (frontier) | pre-check | anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 1605/1712 | $0.0203

Owner ruling 2026-11-07 still holds: one LINE per lane, each lane's figures on
its own line (tests/banners/test_m48_lane_separation.py). M50-26 keeps that and
removes only the repeated head — the head opens the block once, the remaining
lane lines are continuations. Cells, columns and the head itself are unchanged.
"""
from __future__ import annotations

import pytest

from features.banners import turn_record as tr

HEAD = "· router ·"

DECISION = ("impulse (decision) | 1 verdict+1 hold | ×2 | row=1032 ·/jev @ "
            "typesafe | tok 1647/74 | $0.0002 | provider=typesafe "
            "| initiator=agent")
FRONTIER = ("higher-self (frontier) | pre-check | "
            "anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 1605/1712 "
            "| $0.0203")
UNCENSORED = ("shadow-self (uncensored) | render | abliterated-large-v2 "
              "@ venice | tok 900/400 | $0.003000")


@pytest.fixture()
def session():
    tr._RECORDS.clear()
    tr._PENDING_ATTACH.clear()
    return "test-m50-26-one-head"


def _heads(out: str) -> int:
    return str(out or "").count(HEAD)


def test_two_lanes_one_head_per_delivered_body(session):
    """The reproduced defect: two lanes fired, the body must carry ONE head."""
    tr.register(session, DECISION, lane="decision")
    tr.register(session, FRONTIER, lane="frontier")
    body = tr.compose_onto(session, "the answer the reader receives",
                           mode="append")
    assert body.endswith("the answer the reader receives") is False
    assert _heads(body) == 1, body
    # both lanes still reach the reader, each with its OWN figures
    assert "tok 1647/74" in body, body
    assert "tok 1605/1712" in body, body
    assert "$0.0002" in body and "$0.0203" in body, body


def test_three_lanes_still_one_head(session):
    tr.register(session, DECISION, lane="decision")
    tr.register(session, FRONTIER, lane="frontier")
    tr.register(session, UNCENSORED, lane="uncensored")
    # compose_line only: an uncensored segment makes compose_onto refuse to
    # touch the body (M44-02, denial-lane suppression) — that guard is not
    # what this unit changes.
    line = tr.compose_line(session)
    assert _heads(line) == 1, line
    assert len([ln for ln in line.splitlines() if ln.strip()]) == 3, line
    for frag in ("tok 1647/74", "tok 1605/1712", "tok 900/400"):
        assert frag in line, line


def test_head_opens_the_block_and_continuations_do_not_repeat_it(session):
    tr.register(session, DECISION, lane="decision")
    tr.register(session, FRONTIER, lane="frontier")
    line = tr.compose_line(session)
    lines = [ln for ln in line.splitlines() if ln.strip()]
    assert len(lines) == 2, lines                     # one line per lane (M48)
    assert lines[0].startswith(HEAD), lines[0]
    assert HEAD not in lines[1], lines[1]


def test_lane_figures_never_cross_lines_after_the_fix(session):
    """The M48 separation invariant survives the head change."""
    tr.register(session, DECISION, lane="decision")
    tr.register(session, FRONTIER, lane="frontier")
    lines = [ln for ln in tr.compose_line(session).splitlines() if ln.strip()]
    decision = next(ln for ln in lines if "impulse" in ln)
    frontier = next(ln for ln in lines if "higher-self" in ln)
    assert "1605/1712" not in decision, decision
    assert "1647/74" not in frontier, frontier
    assert "claude-sonnet-5.5" not in decision, decision
    assert "typesafe" not in frontier, frontier


def test_a_second_compose_never_stacks_a_second_head(session):
    tr.register(session, DECISION, lane="decision")
    tr.register(session, FRONTIER, lane="frontier")
    once = tr.compose_onto(session, "body", mode="append")
    twice = tr.compose_onto(session, once, mode="append")
    assert once == twice, twice
    assert _heads(twice) == 1, twice
