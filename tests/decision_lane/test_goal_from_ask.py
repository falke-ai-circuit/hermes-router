"""The goal is extracted from the ASK and adapts when the ask changes.

WHAT THIS PINS (Goran, 2026-11-14)
    "Extract goal from ask and change or adapt if it is different from the
    previous ask so that the decision model has a proper, actually correct and
    precise goal."

    So the objective the lane receives is the CURRENT ask — not the session's
    founding ask, and not machine text. `_goal_guard` must:
      1. take the goal from the ask, and REBASE the baseline when the new ask
         differs from the stored one (the audit of lost quantifiers is kept);
      2. keep the stored goal on an ACKNOWLEDGEMENT — "ok" states no new task and
         must not become the objective, nor be read as drift;
      3. REFUSE to derive a goal from a non-human turn (cron wake, dispatch
         brief, startup self-description). Measured 2026-11-14: 115 of 859 fleet
         rows carried such text as the session's goal;
      4. never wipe a goal when nothing is derivable.
"""
from __future__ import annotations

import pytest

from hermes_router import decision as d

SID = "goal-from-ask-session"
ASK_A = "Design the retry and backoff strategy for the outbound webhook system"
ASK_B = "Now size the Q3 capacity plan from last quarter's demand curve"
CRON = ("[IMPORTANT: You are running as a scheduled cron job. DELIVER the "
        "watchdog report and stop]")
BRIEF = "owner: coder=https://coder gateway 8653 artefact_path: /opt/data/tmp/r78u/M45-22.md"


def _guard(objective="", row="", derived="", full=""):
    out = {"final_objective": objective}
    d._goal_guard(out, SID, "_nodb_", row, derived, full or derived or row)
    return out


def test_first_ask_becomes_the_goal(tmp_path):
    db = str(tmp_path / "l.db")
    out = {"final_objective": ASK_A}
    d._goal_guard(out, SID, db, ASK_A, ASK_A, ASK_A)
    assert out["goal_guard"] == "persisted"
    stored = d._goal_read(SID, db)
    assert stored["objective_sha"] == d._goal_sha(ASK_A)


def test_changed_ask_adapts_the_goal(tmp_path):
    """The lane gets what is being asked NOW, not turn one."""
    db = str(tmp_path / "l.db")
    d._goal_guard({"final_objective": ASK_A}, SID, db, ASK_A, ASK_A, ASK_A)

    out = {"final_objective": ASK_B}
    d._goal_guard(out, SID, db, ASK_B, ASK_B, ASK_B)

    assert out["final_objective"] == ASK_B
    assert out["rolling_task"] == ASK_B[:d.SESSION_TASK_CHARS]
    assert out["goal_guard"] == "drift"
    assert out["goal_guard_action"] == "rebased"
    # the baseline moved with the ask
    assert d._goal_read(SID, db)["objective_sha"] == d._goal_sha(ASK_B)


def test_ack_keeps_the_goal_and_is_not_drift(tmp_path):
    db = str(tmp_path / "l.db")
    d._goal_guard({"final_objective": ASK_A}, SID, db, ASK_A, ASK_A, ASK_A)

    out = {"final_objective": "ok"}
    d._goal_guard(out, SID, db, "ok", "ok", "ok")

    assert out["final_objective"] == ASK_A
    assert "webhook" in out["rolling_task"]
    assert out["goal_guard"] == "persisted"


@pytest.mark.parametrize("machine_text", [CRON, BRIEF])
def test_non_human_turn_never_becomes_the_goal(tmp_path, machine_text):
    """A cron wake or dispatch brief states no objective."""
    db = str(tmp_path / "l.db")
    out = {"final_objective": machine_text}
    d._goal_guard(out, SID, db, machine_text, machine_text, machine_text)

    assert out["goal_guard"] == "skipped_non_human"
    assert out["final_objective"] == ""
    assert d._goal_read(SID, db) is None      # nothing was recorded


def test_non_human_turn_keeps_a_real_baseline(tmp_path):
    db = str(tmp_path / "l.db")
    d._goal_guard({"final_objective": ASK_A}, SID, db, ASK_A, ASK_A, ASK_A)

    out = {"final_objective": CRON}
    d._goal_guard(out, SID, db, CRON, CRON, CRON)

    assert out["goal_guard"] == "skipped_non_human"
    assert out["final_objective"] == ASK_A
    assert "webhook" in out["rolling_task"]


def test_nothing_derivable_keeps_the_goal(tmp_path):
    db = str(tmp_path / "l.db")
    d._goal_guard({"final_objective": ASK_A}, SID, db, ASK_A, ASK_A, ASK_A)

    out = {"final_objective": ""}
    d._goal_guard(out, SID, db, "", "", "")

    assert out["final_objective"] == ASK_A
    assert out["goal_guard"] == "persisted"
