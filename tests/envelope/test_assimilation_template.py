"""Pins for the assimilation template (phase-2 U2, plan §3.2).

One template per direction: every lane builds the SAME core slots, and its
lane extras are the registry's CLOSED assimilation key set. These pins hold:

  1. three-lane build — identical core slot order, only extras differ;
  2. per-lane extras accepted, each lane's own closed set;
  3. refusal on unknown extras / unknown lane / a request-only key, never a
     silent drop;
  4. budget + truncation behaves like the request builder and RECORDS loss;
  5. the frontier keys keep their names 1:1 (no consumer breaks).
"""
from __future__ import annotations

import json

from hermes_router.features.envelope import unified as U

CORE_SLOTS = ("schema", "lane", "producer", "route_id", "task_id", "answer",
              "verdict", "revised", "evidence_refs", "limitations", "kind",
              "ts", "extras", "budget", "trimmed")

LANE_EXTRAS = {
    "frontier": {"consult_kind": "consultation",
                 "pov_claims": [{"voice": "skeptic", "weight": 0.6}],
                 "dials": {"skeptic": 0.6}},
    "decision": {"options": [{"id": "opt-1"}], "chosen": "opt-1",
                 "resolver_rule": "max_weight", "outcome_label": "resolved",
                 "diagnosticity": 0.7},
    "uncensored": {"leg": "post", "render": "the render",
                   "acceptance": "accepted", "refusal_class": "",
                   "voice_mode": "voice_stems"},
}


def _build(lane: str, **kwargs):
    kwargs.setdefault("extras", {lane: LANE_EXTRAS[lane]})
    return U.build_assimilation(lane=lane, producer="producer-x",
                                route_id="rid", task_id="t",
                                answer="answer body",
                                verdict={"kind": "ok"},
                                evidence_refs=["a.md"], limitations="none",
                                kind="lane_plan", ts=123.0, **kwargs)


# --- 1. one template, three lanes -----------------------------------------

def test_all_three_lanes_build_the_same_core_slots():
    for lane in ("decision", "frontier", "uncensored"):
        env = _build(lane)
        assert tuple(env) == CORE_SLOTS, "core slots drifted for lane %r" % lane
        assert env["schema"] == "lane-assimilation/1"
        assert env["lane"] == lane
        assert env["kind"] == "lane_plan"
        assert env["ts"] == 123.0
        assert set(env["extras"]) == {lane}
        assert env["budget"]["chars_cap"] > 0
        assert U.validate_assimilation(env) == [], (lane, U.validate_assimilation(env))


def test_lane_extras_are_carried_verbatim():
    for lane, extras in LANE_EXTRAS.items():
        assert _build(lane)["extras"][lane] == extras


# --- 2. empty extras are still a full template ----------------------------

def test_no_extras_still_builds_a_full_envelope():
    env = U.build_assimilation(lane="frontier", answer="a")
    assert tuple(env) == CORE_SLOTS
    assert env["extras"] == {"frontier": {}}
    assert env["verdict"] == {} and env["revised"] is False
    assert env["evidence_refs"] == [] and env["limitations"] == ""
    assert env["kind"] == ""
    assert isinstance(env["ts"], float)


# --- 3. refusals -----------------------------------------------------------

def test_unknown_extras_key_is_refused_per_lane():
    for lane in ("decision", "frontier", "uncensored"):
        assert U.build_assimilation(lane=lane,
                                    extras={lane: {"bogus_key": 1}}) == {}
        errors = U.validate_extras(lane, {lane: {"bogus_key": 1}},
                                   "assimilation")
        assert any("unknown extras" in e for e in errors), errors


def test_request_only_key_is_refused_in_the_assimilation_direction():
    # `paths` is a REQUEST key for frontier; it is not part of the output set
    assert U.build_assimilation(lane="frontier",
                                extras={"frontier": {"paths": []}}) == {}
    # and `pov_claims` is output-only — refused going the other way
    assert U.build_request(lane="frontier",
                           extras={"frontier": {"pov_claims": []}}) == {}


def test_unknown_lane_and_cross_lane_extras_are_refused():
    assert U.build_assimilation(lane="midway") == {}
    assert U.build_assimilation(lane="frontier",
                                extras={"decision": {"chosen": "opt-1"}}) == {}


# --- 4. budget + truncation: ordered, recorded, visible -------------------

def test_over_cap_assimilation_truncates_and_records_the_loss():
    env = U.build_assimilation(
        lane="frontier", answer="a" * 4000, ts=1.0,
        extras={"frontier": {"pov_claims": [{"note": "n" * 2000}],
                             "consult_kind": "consultation"}},
        budget={"chars_cap": 1200,
                "drop_order": ["extras.frontier.pov_claims"]},
    )
    assert env["trimmed"][0] == "extras.frontier.pov_claims"
    assert env["extras"]["frontier"]["pov_claims"] == []
    assert env["extras"]["frontier"]["consult_kind"] == "consultation"
    assert U.CLIP_MARK in env["answer"], "backstop must clip the longest leaf"
    assert len(json.dumps(env, sort_keys=True)) <= 1200
    assert env["budget"] == {"chars_cap": 1200,
                             "drop_order": ["extras.frontier.pov_claims"]}


def test_under_cap_assimilation_records_no_loss():
    env = _build("frontier")
    assert env["trimmed"] == []
    assert env["extras"]["frontier"]["pov_claims"]


# --- 5. frontier key names are 1:1 with what consumers already read -------

def test_frontier_assimilation_key_names_unchanged():
    env = U.build_assimilation(
        lane="frontier", producer="p", route_id="r", task_id="t",
        answer="a", evidence_refs=[], limitations="", kind="frontier_plan",
        extras={"frontier": LANE_EXTRAS["frontier"]})
    for key in ("kind", "producer", "route_id", "task_id", "answer",
                "evidence_refs", "limitations", "ts"):
        assert key in env, "frontier consumer key %r disappeared" % key
    assert set(env["extras"]["frontier"]) == {
        "consult_kind", "pov_claims", "dials"}
