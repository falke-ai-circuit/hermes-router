"""R78U M46-06 — RULE 2 floor semantics (owner ruling).

Ruling (verbatim): "Builder is correct yes, every ask had possibility to
consult depending on triggers".

Contract under test:
  1. EVERY ask keeps the POSSIBILITY of a consult. The FIRST consult of a task
     is never floored — on task turn 1 it fires whenever a trigger routed here.
  2. The per-task cap stands: 1 PRE + 1 POST.
  3. The floor is a REPEAT-consult floor counted in TASK turns: consults after
     the first wait until the task has aged to `consult_min_turns`.
  4. The knob is read from config; changing it moves the refusal boundary with
     no code change.

Red-before-fix: on the M45-25b tree the floor compared SESSION turns and gated
every leg, so test_first_consult_fires_on_task_turn_one and every knob-boundary
assertion fail (session turn 9 with floor 3 refused the first consult outright).
"""
from __future__ import annotations

import pytest

pytest_plugins = ("tests.conftest",)


@pytest.fixture()
def budget(monkeypatch):
    from hermes_router import consult_budget as cb, advisory_consumption as ac

    cb.reset()
    monkeypatch.setattr(ac, "_TURN_NO", {})
    return cb


def _set_turn(budget, session_id: str, n: int) -> None:
    from hermes_router import advisory_consumption as ac

    ac._TURN_NO[session_id] = n


def _knobs(monkeypatch, floor: int, cap: int = 1, post_floor: int = None) -> None:
    """Pin both consult dials. `post_floor` defaults to `floor` so a test that
    moves the age boundary moves the POST's own dial too (M46-07 split the
    PRE->POST gap onto `consult_post_min_turns`)."""
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": floor,
                                 "consult_post_min_turns": (floor if post_floor is None
                                                            else post_floor),
                                 "consult_max_per_task": cap})


def test_first_consult_fires_on_task_turn_one_even_far_into_the_session(
        budget, monkeypatch):
    """CONTRACT 1: the floor never suppresses a task's FIRST consult.

    Session turn 9 with floor 3 is exactly the case M45-25b killed: the old
    session-based floor refused it. Here the task was just minted, task turn 1,
    so the trigger-eligible ask must produce its PRE consult.
    """
    _knobs(monkeypatch, floor=3)
    session, task = "s-m4606-first", "t-m4606-first"
    _set_turn(budget, session, 9)

    gate = budget.check(session, task, leg="pre")
    assert gate["allow"] is True, gate
    assert gate["reason"] == "", gate
    assert gate["task_turn"] == 1, gate
    assert budget.counts(session, task) == {"pre": 1}


def test_second_consult_before_the_floor_is_refused_and_recorded(
        budget, monkeypatch):
    """CONTRACT 3: repeat below the floor is REFUSED and RECORDED, never dropped."""
    rows = []
    from hermes_router import decision as dm

    monkeypatch.setattr(dm, "ledger_write", lambda row, db_path="": rows.append(row))
    _knobs(monkeypatch, floor=3)
    session, task = "s-m4606-repeat", "t-m4606-repeat"
    _set_turn(budget, session, 4)

    assert budget.check(session, task, leg="pre")["allow"] is True  # first
    second = budget.check(session, task, leg="post")
    assert second["allow"] is False, second
    assert second["reason"] == "below_min_turns", second
    assert second["task_turn"] == 1, second
    assert budget.counts(session, task) == {"pre": 1}

    capped = [r for r in rows if r.get("trigger") == "consult_capped"]
    assert len(capped) == 1, rows
    assert capped[0].get("choice") == "refused"
    assert str(capped[0].get("task_id")) == task
    assert "below_min_turns" in str(capped[0].get("verdict_json")), capped[0]


def test_task_past_the_floor_gets_one_pre_one_post_and_no_more(
        budget, monkeypatch):
    """CONTRACTS 2+3: aged task spends 1 PRE + 1 POST; everything else capped.

    M46-07: the PRE->POST age boundary is `consult_post_min_turns`; `_knobs`
    pins it alongside `consult_min_turns` so this test still moves the boundary
    it is named for (the cap assertions are untouched).
    """
    _knobs(monkeypatch, floor=2)
    session, task = "s-m4606-aged", "t-m4606-aged"

    _set_turn(budget, session, 100)
    assert budget.check(session, task, leg="pre")["allow"] is True   # task turn 1
    assert budget.check(session, task, leg="post")["allow"] is False  # floored

    _set_turn(budget, session, 101)                                   # task turn 2
    assert budget.check(session, task, leg="post")["allow"] is True

    calls, capped = 2, 0
    for _turn in range(102, 112):                                     # task turns 3..12
        _set_turn(budget, session, _turn)
        for leg in ("pre", "post"):
            gate = budget.check(session, task, leg=leg)
            if gate["allow"]:
                calls += 1
            else:
                capped += 1
                assert gate["reason"] == "consult_capped", gate

    assert calls == 2, calls
    assert capped == 20, capped
    assert budget.counts(session, task) == {"pre": 1, "post": 1}


def test_knob_moves_the_refusal_boundary_without_a_code_change(budget, monkeypatch):
    """CONTRACT 4: floor 2 vs 4 refuses the SAME repeat on different task turns."""
    outcomes = {}
    for floor in (2, 4):
        budget.reset()
        _knobs(monkeypatch, floor=floor, cap=3)
        session, task = "s-m4606-knob-%d" % floor, "t-m4606-knob-%d" % floor
        _set_turn(budget, session, 50)
        first = budget.check(session, task, leg="pre")
        _set_turn(budget, session, 51)          # task turn 2
        second = budget.check(session, task, leg="pre")
        _set_turn(budget, session, 53)          # task turn 4
        fourth = budget.check(session, task, leg="pre")
        outcomes[floor] = (first["allow"], second["allow"], fourth["allow"],
                           second["reason"])

    assert outcomes[2] == (True, True, True, ""), outcomes[2]
    assert outcomes[4] == (True, False, True, "below_min_turns"), outcomes[4]


def test_unknown_session_turn_fails_open(budget, monkeypatch):
    """A dispatch outside the turn lifecycle (turn 0) is never floored."""
    _knobs(monkeypatch, floor=5)
    session, task = "s-m4606-unknown", "t-m4606-unknown"

    assert budget.check(session, task, leg="pre")["allow"] is True
    assert budget.check(session, task, leg="pre")["reason"] == "consult_capped"
