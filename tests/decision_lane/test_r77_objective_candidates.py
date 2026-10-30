"""R77 — objective-anchored candidates.

Measured defect (board_differential, 2026-10-30): identical ask + identical tool
window on coder/reviewer/analyst/orchestrator produced the SAME top move ("read
reflex_lane.py first" for all four — that filename was in the window). The board
was mined only from the window, so objective-level moves could not win a
selection they never contested.

These pins fix the two halves of the contract:
  * the session anchor contributes candidates (phrased against the objective);
  * with no usable objective the board is EXACTLY the pre-R77 window board.
"""
from __future__ import annotations

from hermes_router import decision_extract as dx

WINDOW = ("write_file ok: /opt/data/plugins/hermes_router/reflex_lane.py (+93 -12)\n"
          "pytest: 7 failed, 2669 passed, 3 skipped in 420.61s\n"
          "note: baseline failures unchanged; no new reds beyond "
          "tests/baseline_failures.txt\n")

OBJECTIVE = ("TASK (diagnosis ONLY — read, trace, conclude. No code changes, no fixes.) "
             "Diagnose why the decision lane board is identical across agents.")


def _labels(cands):
    return [c["label"] for c in cands]


def test_objective_candidates_reach_the_board():
    plain = dx.candidate_set(WINDOW, cap=7, ask="")
    with_obj = dx.candidate_set(WINDOW, cap=7, ask="", rolling_task=OBJECTIVE)
    assert plain and with_obj
    # the objective's own move is on the board now
    assert any("the objective" in lbl for lbl in _labels(with_obj))
    # and it is not a paraphrase of the window's own read of the same file
    assert any(c["move"] == "hold" for c in with_obj)
    # cap semantics unchanged
    assert len(with_obj) <= 7


def test_objective_candidate_carries_the_objective_not_a_generic_template():
    cands = dx.candidate_set(WINDOW, cap=7, ask="", rolling_task=OBJECTIVE)
    obj = [c for c in cands if "the objective" in c["label"]]
    assert obj and "TASK (diagnosis ONLY" in obj[0]["label"]


def test_no_objective_falls_back_to_the_exact_window_board():
    """The pre-R77 board, byte for byte — this is the fallback contract."""
    assert (dx.candidate_set(WINDOW, cap=7, ask="") ==
            dx.candidate_set(WINDOW, cap=7, ask="", objective="", rolling_task=""))


def test_identical_objective_and_rolling_task_do_not_duplicate_candidates():
    """Defect 3 (single-dominant-ask collapse) is reported, not fixed: when the
    two fields are the same string the board still carries ONE set of
    objective moves, never a doubled board."""
    same = dx.candidate_set(WINDOW, cap=7, ask="", objective=OBJECTIVE,
                            rolling_task=OBJECTIVE)
    assert len(_labels(same)) == len(set(_labels(same)))


def test_paraphrase_of_one_move_is_dropped_within_its_class():
    a = {"move": "run_check", "kind": "run", "target": "",
         "label": "run the check or tests now and read the result"}
    b = {"move": "run_check", "kind": "run", "target": "",
         "label": "run the check or tests now and read the result please"}
    merged = dx._merge_boards([b], [a], 7)
    assert len([c for c in merged if c["kind"] == "run"]) == 1


def test_read_and_change_of_one_file_are_not_collapsed():
    """Same tokens, different move: the read option must survive next to the edit."""
    read = {"move": "inspect", "kind": "read", "target": "/x/a.py", "label": "read a.py first"}
    edit = {"move": "fix", "kind": "edit", "target": "/x/a.py", "label": "change a.py"}
    assert not dx._same_move(read, edit)


def test_gate_thresholds_unchanged():
    assert dx.DECISIVE_MARGIN == 0.20
