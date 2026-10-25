"""R58 — the mindset adjust rides the SAME fork request as the choice.

The decision lane already sends the full envelope (agent card, causal context,
surrounding context) to the system-one backend when a fork is detected. The
requirement is that the mindset adjust comes back in that same request — no
second call — because mindset is the secondary modelling that hands the main
model quantized values to interpret.

Typesafe's native schema supports a `score` question: ordered rubric levels
whose POSITION is the score, answered as a probability-weighted average so the
answer is fractional. Two score questions (scope, rigour) are added to the same
`questions` object as the choice question.

The verdict contract says mindset values are ADJUSTMENTS relative to the
envelope's current stance, so the absolute optimum read off the rubric is
differenced before it is handed on. A drive with an unreadable stance is
skipped rather than defaulted: a delta from an unknown baseline is not a
measurement.
"""

import json

import pytest

from hermes_router import decision as D
from hermes_router import decision_midturn as dm

ENV = {"mindset": {"scope": 0.2, "rigour": -0.4},
       "options": [{"id": "opt-1"}, {"id": "opt-2"}]}


def _verdict(answers, env=None):
    data = {"answers": dict({"choice": {"choice": "opt-1", "confidence": 0.8}},
                            **answers)}
    return json.loads(D._systemone_verdict_content(data, env or ENV))


@pytest.fixture(autouse=True)
def _quiet_log(monkeypatch):
    monkeypatch.setattr(dm, "_log", lambda *a, **k: None)


# --- the request carries the mindset question -------------------------------

def test_payload_asks_for_mindset_in_the_same_request():
    payload = D._systemone_native_payload(ENV, {"frame": "coder"})
    qs = payload.get("questions") or {}
    assert set(qs) == {"choice", "scope", "rigour"}, (
        "the mindset questions must ride the SAME questions object as the "
        "choice, not a second request")


def test_mindset_questions_use_the_score_type():
    qs = D._systemone_native_payload(ENV, {})["questions"]
    for drive in ("scope", "rigour"):
        assert qs[drive]["type"] == "score", (
            f"{drive} must be a score question; only 'score' returns a "
            "fractional value the dial can use")
        assert qs[drive].get("instructions"), f"{drive} has no instructions"


def test_score_criteria_stay_inside_the_api_bounds():
    # systemone schema: criteria is an array with minItems 1, maxItems 10
    qs = D._systemone_native_payload(ENV, {})["questions"]
    for drive in ("scope", "rigour"):
        crit = qs[drive]["criteria"]
        assert 1 <= len(crit) <= 10, f"{drive} criteria {len(crit)} out of bounds"
        assert all(isinstance(c, str) and c.strip() for c in crit)


def test_rubrics_are_ordered_low_to_high():
    # position IS the score, so the rubric must run narrow->wide / act->verify
    assert "narrow" in D.MINDSET_SCORE_LEVELS[0].lower()
    assert "wide" in D.MINDSET_SCORE_LEVELS[-1].lower()
    assert "act now" in D.MINDSET_RIGOUR_LEVELS[0].lower()
    assert "verify" in D.MINDSET_RIGOUR_LEVELS[-1].lower()


# --- score -> signed delta ---------------------------------------------------

def test_score_maps_to_optimal_dial_then_differences():
    # 5 levels -> 0..4 maps to -1..+1; score 3 => +0.5
    v = _verdict({"scope": {"type": "score", "score": 3.0},
                  "rigour": {"type": "score", "score": 1.0}})
    # scope optimal +0.5 vs current +0.2 -> +0.3
    # rigour optimal -0.5 vs current -0.4 -> -0.1
    assert v["mindset"]["scope"] == pytest.approx(0.3, abs=1e-6)
    assert v["mindset"]["rigour"] == pytest.approx(-0.1, abs=1e-6)


def test_delta_is_relative_not_absolute():
    # SAME answers, different current stance -> different deltas
    answers = {"scope": {"type": "score", "score": 4.0}}  # optimal +1.0
    low = _verdict(answers, {"mindset": {"scope": -0.5, "rigour": 0.0},
                             "options": [{"id": "opt-1"}]})
    high = _verdict(answers, {"mindset": {"scope": 0.9, "rigour": 0.0},
                              "options": [{"id": "opt-1"}]})
    assert low["mindset"]["scope"] == pytest.approx(1.0, abs=1e-6)   # +1.0-(-0.5), clamped
    assert high["mindset"]["scope"] == pytest.approx(0.1, abs=1e-6)  # +1.0-(+0.9)
    assert low["mindset"]["scope"] != high["mindset"]["scope"], (
        "the value is an ADJUSTMENT; an absolute read would give the same "
        "number for both stances")


def test_midpoint_score_is_no_change():
    # score 2 of 0..4 => 0.0 optimum; current 0.0 => zero delta
    v = _verdict({"scope": {"type": "score", "score": 2.0}},
                 {"mindset": {"scope": 0.0, "rigour": 0.0},
                  "options": [{"id": "opt-1"}]})
    assert v["mindset"]["scope"] == pytest.approx(0.0, abs=1e-6)


def test_deltas_are_clamped_to_the_dial():
    v = _verdict({"scope": {"type": "score", "score": 99.0},
                  "rigour": {"type": "score", "score": -99.0}})
    assert -1.0 <= v["mindset"]["scope"] <= 1.0
    assert -1.0 <= v["mindset"]["rigour"] <= 1.0


# --- fail-open: absence must not become a measurement -----------------------

def test_no_score_answers_means_no_mindset_key():
    v = _verdict({})
    assert "mindset" not in v, (
        "without score answers the verdict must look exactly like before, "
        "never a zero-sized adjustment nobody measured")


def test_unreadable_stance_is_skipped_not_defaulted():
    v = _verdict({"scope": {"type": "score", "score": 4.0}},
                 {"mindset": {"scope": None, "rigour": None},
                  "options": [{"id": "opt-1"}]})
    assert "mindset" not in v, (
        "a delta from an unknown baseline is not a measurement")


def test_missing_envelope_mindset_is_skipped():
    v = _verdict({"scope": {"type": "score", "score": 4.0}},
                 {"options": [{"id": "opt-1"}]})
    assert "mindset" not in v


def test_non_numeric_and_odd_scores_are_ignored():
    v = _verdict({"scope": {"type": "score", "score": "high"},
                  "rigour": {"type": "score", "score": True}})
    assert "mindset" not in v or v["mindset"] == {}


def test_malformed_answers_do_not_raise():
    assert D._systemone_mindset_delta(None, ENV) == {}
    assert D._systemone_mindset_delta("nope", ENV) == {}
    assert D._systemone_mindset_delta({}, ENV) == {}


def test_out_of_set_choice_still_rejects_the_whole_verdict():
    # the mindset addition must not weaken choice validation
    data = {"answers": {"choice": {"choice": "opt-9", "confidence": 0.9},
                        "scope": {"type": "score", "score": 4.0}}}
    assert D._systemone_verdict_content(data, ENV) == ""


# --- end to end: the adjust actually reaches the delivery surface ----------

def test_large_delta_emits_a_stance_block():
    env = {"mindset": {"scope": -0.85, "rigour": -0.85},
           "options": [{"id": "opt-1"}]}
    v = _verdict({"scope": {"type": "score", "score": 4.0},
                  "rigour": {"type": "score", "score": 4.0}}, env)
    block = dm._midturn_mindset_block(v, env, "test-r58")
    assert "widen" in block.lower(), f"expected a widening line, got {block!r}"
    assert "verify" in block.lower(), f"expected a rigour line, got {block!r}"


def test_inverse_delta_emits_the_opposite_lines():
    env = {"mindset": {"scope": 0.9, "rigour": 0.9},
           "options": [{"id": "opt-1"}]}
    v = _verdict({"scope": {"type": "score", "score": 0.0},
                  "rigour": {"type": "score", "score": 0.0}}, env)
    block = dm._midturn_mindset_block(v, env, "test-r58b")
    assert "narrow" in block.lower()
    assert block != "", "the inverse direction must also produce lines"


def test_in_band_delta_stays_silent():
    # delta 0.3 / -0.1 are inside BAND 0.5 -> no block at all
    v = _verdict({"scope": {"type": "score", "score": 3.0},
                  "rigour": {"type": "score", "score": 1.0}})
    assert dm._midturn_mindset_block(v, ENV, "test-r58c") == ""


def test_verdict_shape_passes_validation():
    # the added key must be one the verdict validator already allows
    v = _verdict({"scope": {"type": "score", "score": 3.0}})
    # validate_verdict takes the JSON STRING the backend returned
    cleaned, reason = D.validate_verdict(json.dumps(v), ENV)
    assert cleaned is not None, f"verdict rejected: {reason}"
    assert "mindset" in cleaned
