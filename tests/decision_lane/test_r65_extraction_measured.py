"""R65 — extraction, measured. 499 real failure points, 11 agents, 3 disjoint samples.

Ground truth: the file the agent ACTUALLY edited next, taken from its own transcript. The
candidate pool is exactly the paths the evidence makes visible, so chance is well defined
(1/k, ~0.21) and every trivial heuristic can be scored on the same points.

Measured (see /opt/data/tmp/extract_proof):
  choice distribution over candidates   0.700 / 0.797 / 0.767 top-1   (pooled 0.764)
  per-candidate noul statements         0.655 / 0.759 top-1
  most-mentioned-path heuristic         0.390
  last-mentioned-path heuristic         0.487
  chance                                0.21
  mean top weight when right 0.774, when wrong 0.549   -> a usable gate

These tests pin the parts that measurement selected, so a future refactor cannot quietly
revert them: the distribution shape, the gate thresholds, and the fail-open boundaries.
"""
from __future__ import annotations

import pytest

from hermes_router import decision_extract as dx
from hermes_router import reflex_lane as rl

WINDOW = ("Traceback (most recent call last):\n  File \"/opt/data/app/src/render.py\", "
          "line 12\nImportError: cannot import name 'cache' from /opt/data/app/src/cache.py")


def test_candidates_name_the_object_before_the_move_class():
    """The measured win came from file-bearing candidates; generic classes ride alongside."""
    c = dx.candidate_set(WINDOW)
    assert len(c) >= 3
    assert any(o["target"].endswith("render.py") for o in c)
    assert any(o["move"] == "fix" for o in c)
    assert any(o["kind"] == "run" for o in c)


def test_candidate_set_is_empty_without_a_file_to_act_on():
    """With no object in play the block would be a lecture; the lane stays quiet."""
    assert dx.candidate_set("nothing in particular happened here") == []


def test_one_choice_question_not_a_battery_of_nouls():
    """Measurement, not taste: the choice distribution beat independent nouls on every metric."""
    cands = dx.candidate_set(WINDOW)
    p = dx.payload("state", cands)
    types = [q["type"] for q in p["questions"].values()]
    assert types.count("choice") == 1
    assert types.count("noul") == 0
    assert "scope" in p["questions"] and "rigour" in p["questions"]
    assert len(p["questions"]["action"]["criteria"]) == len(cands)


def test_a_distribution_that_sums_to_one_survives_as_one():
    cands = dx.candidate_set(WINDOW)
    data = {"answers": {"action": {"probabilities": {"a1": 0.6, "a2": 0.25, "a3": 0.15}},
                        "scope": {"score": 1}, "rigour": {"score": 4}}}
    got = dx.parse(data, cands)
    assert sum(o["p"] for o in got["options"]) == pytest.approx(1.0, abs=0.02)
    assert dx.render(got).count("%") >= 2


def test_unnormalized_probabilities_are_rescaled_not_trusted():
    cands = dx.candidate_set("see /opt/data/tmp/a.py and /opt/data/tmp/b.py")
    data = {"answers": {"action": {"probabilities": {"a1": 3.0, "a2": 1.0}}}}
    got = dx.parse(data, cands)
    assert sum(o["p"] for o in got["options"]) == pytest.approx(1.0, abs=0.01)


def test_gate_silences_a_flat_distribution():
    """The lane's own noise band: 0.32 vs 0.30 must not steer anything."""
    cands = dx.candidate_set(WINDOW)
    data = {"answers": {"action": {"probabilities": {"a1": 0.32, "a2": 0.30, "a3": 0.38}}}}
    got = dx.parse(data, cands)
    ok, why = dx.gate(got)
    assert ok is False
    assert why in ("top_below_floor", "no_separation")


def test_gate_passes_a_decisive_reading():
    cands = dx.candidate_set(WINDOW)
    data = {"answers": {"action": {"probabilities": {"a1": 0.78, "a2": 0.14, "a3": 0.08}}}}
    ok, why = dx.gate(dx.parse(data, cands))
    assert ok is True and why == "ok"


def test_gate_never_steers_toward_stopping():
    """'stop and report' as the top pick is not something the lane pushes into a turn."""
    cands = [{"move": "stop_report", "kind": "none", "target": "", "label": "stop and report"},
             {"move": "inspect", "kind": "read", "target": "/opt/data/x.py", "label": "read x"}]
    got = dx.parse({"answers": {"action": {"probabilities": {"a1": 0.8, "a2": 0.2}}}}, cands)
    assert dx.gate(got)[0] is False


def test_mindset_deltas_ride_the_same_call():
    cands = dx.candidate_set(WINDOW)
    got = dx.parse({"answers": {"action": {"probabilities": {"a1": 0.7, "a2": 0.3}},
                                "scope": {"score": 0}, "rigour": {"score": 4}}}, cands)
    assert got["mindset"] == {"scope": -1.0, "rigour": 1.0}


def test_parse_never_raises_on_garbage():
    cands = dx.candidate_set(WINDOW)
    for junk in (None, {}, {"answers": {}}, {"answers": {"action": {"probabilities": "x"}}},
                 {"answers": {"action": {"probabilities": {"a1": "0.9"}}}}):
        assert dx.parse(junk, cands) in (None, ) or isinstance(dx.parse(junk, cands), dict)


def test_over_budget_block_emits_nothing():
    got = {"options": [{"p": 0.6, "label": "a" * 300, "move": "fix", "kind": "edit", "target": ""},
                       {"p": 0.4, "label": "b" * 300, "move": "fix", "kind": "edit", "target": ""}],
           "mindset": {}}
    assert dx.render(got, limit=120) == ""


def test_lane_stays_silent_without_a_file_to_act_on(monkeypatch):
    """No object named means no block — the gate that keeps this from becoming commentary."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: pytest.fail("must not call"))
    assert rl.fire("s1", "terminal", "just a short note about nothing",
                   {"hermes_router": {"reflex_weights": {"enabled": True}}}) == ""
