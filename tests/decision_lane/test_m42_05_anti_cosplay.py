"""M42-05 pins — anti-cosplay certification + the goal-guard store label.

Two independent defects, one file:

A. A candidate lane must not pass certification by emitting a landed lane's
   frame. `render_payload.frame_distinctness` measures 1 − Jaccard over 3-word
   shingles with numbers masked; `lane_certify.certify` rejects a candidate
   that stays closer than the floor to ANY landed frame. NOT wired into
   delivery in this unit — a rejection is counted (`bump_counter`) only.

B. `decision._goal_guard` labelled a turn "persisted" from a discarded
   `_goal_write` return. With no store the write returns False and the label
   must say "unverified"; on the healthy path the stored fingerprint must be
   the fingerprint of the objective the guard served.

Measured values on real `render_impulse_frame` output are asserted, not
invented: an identical copy scores 0.0, a one-phrase slot swap 0.136, and a
frame from a different register/band/option set 0.827 (see the artefact
/opt/data/tmp/r78u/M42-05_FIX.md for the full table).
"""
from __future__ import annotations

import pytest

from hermes_router import decision as d
from hermes_router import render_payload as rp
from hermes_router.lane_certify import certify

SID = "m42-05-session"


def _env(labels, weights, band, evidence, register=None):
    """A real impulse envelope: options + mechanical weighting block."""
    env = {
        "options": [{"id": "opt-%d" % (i + 1), "label": label}
                    for i, label in enumerate(labels)],
        "weighting": {
            "weights": {"opt-%d" % (i + 1): w for i, w in enumerate(weights)},
            "band": band,
            "evidence": list(evidence),
        },
    }
    if register is not None:
        env["agent_frame"] = register
    return env


def _landed_frame():
    """The landed lane's frame: two options, strong band, no persona slot."""
    return rp.render_impulse_frame({}, _env(
        ["ship the parser", "rewrite the docs"], [0.55, 0.45], "strong",
        ["two routes disagreed on the escape path"]))


def _slot_swapped_frame():
    """Cosplay shape: SAME weights, band and evidence; one phrase changed."""
    return rp.render_impulse_frame({}, _env(
        ["ship the parser", "rewrite the manual"], [0.55, 0.45], "strong",
        ["two routes disagreed on the escape path"]))


def _different_frame():
    """A genuinely different lane: other options, band, evidence and register."""
    return rp.render_impulse_frame({}, _env(
        ["hold the release", "publish the audit", "freeze the schema",
         "rebuild the index"], [0.4, 0.3, 0.2, 0.1], "weak",
        ["the vendor shipped an unexpected build"],
        register="a careful engineer who weighs each move before committing"
                 " and names the cost out loud"))


# ---------------------------------------------------------------------------
# A. anti-cosplay
# ---------------------------------------------------------------------------

def test_identical_copy_of_a_landed_frame_is_rejected():
    """(a) byte-identical copy: distinctness 0.0, certification False."""
    landed = _landed_frame()
    assert landed, "fixture produced no frame — the pin would be vacuous"
    score = rp.frame_distinctness(landed, landed)
    assert score == 0.0
    ok, scores = certify(landed, [landed])
    assert ok is False
    assert scores == [0.0]
    assert d.bump_counter("lane_certify_reject") >= 1


def test_slot_swapped_copy_is_rejected():
    """(b) one phrase changed, same weights/band/evidence: still a copy."""
    landed = _landed_frame()
    candidate = _slot_swapped_frame()
    assert candidate != landed, "the swap did not render — nothing is exercised"
    # the mechanical parts really are identical: only the label pair differs
    assert "pulls 0.55" in candidate and "pulls 0.45" in candidate
    assert "band=strong" in candidate
    score = rp.frame_distinctness(candidate, landed)
    assert 0.0 < score < 0.8, "measured %.4f" % score
    ok, scores = certify(candidate, [landed])
    assert ok is False
    assert scores and scores[0] < 0.8
    assert d.bump_counter("lane_certify_reject") >= 1


def test_genuinely_different_frame_certifies():
    """(c) the check must not fail everything: a different frame passes."""
    landed = _landed_frame()
    candidate = _different_frame()
    score = rp.frame_distinctness(candidate, landed)
    assert score >= 0.8, "measured %.4f" % score
    ok, scores = certify(candidate, [landed])
    assert ok is True
    assert scores == [score]


def test_empty_landed_list_fails_closed():
    """(d) no landed frames = no evidence of distinctness = no pass."""
    ok, scores = certify(_landed_frame(), [])
    assert ok is False
    assert scores == []


def test_numbers_are_masked_before_comparison():
    """A weight-only difference is not a distinct lane (M42-05 masking rule)."""
    a = "the fork surfaces as: ship the parser pulls 0.55 | band=strong"
    b = "the fork surfaces as: ship the parser pulls 0.90 | band=strong"
    assert rp.frame_distinctness(a, b) == 0.0


# ---------------------------------------------------------------------------
# B. goal-guard label
# ---------------------------------------------------------------------------

def test_goal_guard_label_requires_a_row(tmp_path, monkeypatch):
    """No store -> the write returns False -> the label is NOT 'persisted'."""
    db = str(tmp_path / "ledger.db")
    out = {"final_objective": "rebuild the parser and only touch escapes"}
    monkeypatch.setattr(d, "_ledger_connect", lambda *a, **k: None)
    d._goal_guard(out, SID, db, out["final_objective"])
    assert out["goal_guard"] != "persisted"
    assert out["goal_guard"] == "unverified"
    assert d._goal_read(SID, db) is None       # nothing was written, and the
    #                                            label no longer claims there was


def test_goal_guard_healthy_path_records_the_served_objective(tmp_path):
    """Write succeeds -> 'persisted' AND the stored sha is the served objective."""
    db = str(tmp_path / "ledger.db")
    out = {"final_objective": "rebuild the parser and only touch escapes"}
    d._goal_guard(out, SID, db, out["final_objective"])
    assert out["goal_guard"] == "persisted"
    stored = d._goal_read(SID, db)
    assert stored is not None
    assert stored["objective_sha"] == d._goal_sha(out["final_objective"])


def test_goal_guard_advance_write_refusal_is_not_persisted(tmp_path, monkeypatch):
    """Second turn advances rolling text; a refused ADVANCE write is not persisted."""
    db = str(tmp_path / "ledger.db")
    objective = "rebuild the parser"
    first = {"final_objective": objective}
    d._goal_guard(first, SID, db, objective)
    assert first["goal_guard"] == "persisted"

    # read still works (the row exists), the write refuses
    monkeypatch.setattr(d, "_goal_write", lambda *a, **k: False)
    second = {"final_objective": objective}
    d._goal_guard(second, SID, db, "and write the migration notes")
    assert second["rolling_task"] == "and write the migration notes"
    assert second["goal_guard"] == "unverified"
