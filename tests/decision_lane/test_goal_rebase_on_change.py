"""The turn's own ask wins: a changed objective REBASES, it is not overwritten.

WHAT THIS PINS (2026-11-14, Goran's live report)
    Agents worked the current topic but closed the turn on the PREVIOUS thematic.
    `_goal_guard` installed the STORED objective over the current derivation
    (`out["final_objective"] = stored["objective"]`) and then only LABELLED the
    mismatch "drift". `final_objective` reaches the decision lane through the
    consult context (decision.py:2950), so the agent's goal framing pointed at an
    older ask while its messages carried the new one.

    Correct behaviour, pinned here:
      * a genuinely different objective REBASES — the current ask is served and
        the ledger baseline moves to it (the audit of lost tokens is retained);
      * an ACKNOWLEDGEMENT ("ok") still retains the session's goal — it states no
        new task, and it must not be read as drift just because its text differs;
      * an empty derivation never wipes the goal.

    The ack case is the reason the ordering matters: the drift check used to run
    first, so every "ok" was labelled drift and its two-character body installed
    as the objective.
"""
from __future__ import annotations

import pytest

from hermes_router import decision as d

SID = "goal-rebase-session"
GOAL_A = "Design the retry and backoff strategy for the outbound webhook system"
GOAL_B = "Now size the Q3 capacity plan from last quarter's demand curve"


def _first_turn(db: str, objective: str = GOAL_A) -> dict:
    """Turn 1: the session's baseline is written from the first real ask."""
    out = {"final_objective": objective}
    d._goal_guard(out, SID, db, objective, objective, objective)
    assert out["goal_guard"] == "persisted"
    return out


def test_changed_objective_rebases_and_serves_the_current_ask(tmp_path):
    db = str(tmp_path / "ledger.db")
    _first_turn(db)

    second = {"final_objective": GOAL_B}
    d._goal_guard(second, SID, db, GOAL_B, GOAL_B, GOAL_B)

    # the CURRENT ask is what the lane is told the goal is
    assert second["final_objective"] == GOAL_B
    assert second["rolling_task"] == GOAL_B[:d.SESSION_TASK_CHARS]
    # and the audit still reports that the objective moved
    assert second["goal_guard"] == "drift"
    assert second["goal_guard_action"] == "rebased"


def test_rebase_moves_the_stored_baseline(tmp_path):
    db = str(tmp_path / "ledger.db")
    _first_turn(db)
    d._goal_guard({"final_objective": GOAL_B}, SID, db, GOAL_B, GOAL_B, GOAL_B)

    stored = d._goal_read(SID, db)
    assert stored is not None
    assert stored["objective_sha"] == d._goal_sha(GOAL_B)


def test_ack_retains_the_goal_instead_of_becoming_it(tmp_path):
    db = str(tmp_path / "ledger.db")
    _first_turn(db)

    second = {"final_objective": "ok"}
    d._goal_guard(second, SID, db, "ok", "ok", "ok")

    # "ok" is not a new objective; the session keeps what it is FOR
    assert second["final_objective"] == GOAL_A
    assert "webhook" in second["rolling_task"]
    assert second["goal_guard"] == "persisted"


def test_empty_derivation_never_wipes_the_goal(tmp_path):
    db = str(tmp_path / "ledger.db")
    _first_turn(db)

    second = {"final_objective": ""}
    d._goal_guard(second, SID, db, "", "", "")

    assert second["final_objective"] == GOAL_A
    assert second["goal_guard"] == "persisted"
