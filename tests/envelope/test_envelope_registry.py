"""Pins for the lane registry (phase-2 U1, plan §3.1).

The registry is the ONE place a lane and its closed extras key sets live, for
BOTH directions. These pins hold the properties the plan names:

  1. request AND assimilation key sets are DERIVED from the one table (no
     duplicated literals: the old request sets moved out of unified.py);
  2. an unknown lane / unknown key / unknown direction is still a REFUSAL —
     never a silent drop — in both directions;
  3. a NEW fake lane registered at runtime builds in the request direction
     without touching a builder.
     (U2 extends 3 to the assimilation direction; the fake lane declares both
     sets here so the registry contract is pinned from the start.)
"""
from __future__ import annotations

from hermes_router.features.envelope import registry as R
from hermes_router.features.envelope import unified as U

ORIGINAL_LANES = {"decision": {"request", "assimilation"},
                  "frontier": {"request", "assimilation"},
                  "uncensored": {"request", "assimilation"}}


# --- 1. one table, both directions derived --------------------------------

def test_registry_declares_every_lane_in_both_directions():
    for lane in ("decision", "frontier", "uncensored"):
        assert lane in R.LANES
        assert set(R.LANES[lane]) == {"request", "assimilation"}
        assert isinstance(R.LANES[lane]["request"], frozenset)
        assert isinstance(R.LANES[lane]["assimilation"], frozenset)
        assert R.LANES[lane]["request"], "request key set must not be empty"
        assert R.LANES[lane]["assimilation"], "assimilation set must not be empty"


def test_derived_key_sets_are_the_store_not_copies():
    # EXTRAS_KEYS / ASSIM_EXTRAS_KEYS are views over LANES: changing the table
    # is visible through them without rebuilding either direction.
    assert U.EXTRAS_KEYS is R.EXTRAS_KEYS
    assert U.ASSIM_EXTRAS_KEYS is R.ASSIM_EXTRAS_KEYS
    assert set(U.EXTRAS_KEYS) == set(R.LANES)
    assert set(U.ASSIM_EXTRAS_KEYS) == set(R.LANES)
    for lane in R.LANES:
        assert U.EXTRAS_KEYS[lane] == R.LANES[lane]["request"]
        assert U.ASSIM_EXTRAS_KEYS[lane] == R.LANES[lane]["assimilation"]


def test_request_key_sets_moved_here_verbatim():
    # the literals that used to live in unified.py, asserted at the new home
    assert U.EXTRAS_KEYS["decision"] == frozenset({
        "options", "causal_context", "question", "guidance", "weighting",
        "fork_class", "slice", "task", "digest", "surrounding_context",
        "frame_context_chars_used"})
    assert U.EXTRAS_KEYS["frontier"] == frozenset({
        "paths", "evidence_refs", "consult_kind", "route_id", "kind"})
    assert U.EXTRAS_KEYS["uncensored"] == frozenset({
        "leg", "work_text", "voice_mode", "close_class"})


def test_assimilation_key_sets_match_the_plan():
    assert U.ASSIM_EXTRAS_KEYS["frontier"] == frozenset({
        "consult_kind", "pov_claims", "dials"})
    assert U.ASSIM_EXTRAS_KEYS["decision"] == frozenset({
        "options", "chosen", "resolver_rule", "outcome_label", "diagnosticity"})
    assert U.ASSIM_EXTRAS_KEYS["uncensored"] == frozenset({
        "leg", "render", "acceptance", "refusal_class", "voice_mode"})


# --- 2. refusals preserved, in both directions ----------------------------

def test_unknown_lane_is_refused_in_both_directions():
    assert U.validate_extras("midway", {}) == ["unknown lane: 'midway'"]
    assert U.validate_extras("midway", {}, "assimilation") == [
        "unknown lane: 'midway'"]
    assert R.extras_keys("midway") == frozenset()
    assert R.extras_keys("midway", "assimilation") == frozenset()


def test_unknown_key_is_refused_per_direction():
    assert U.validate_extras("frontier", {"frontier": {"paths": []}}) == []
    assert U.validate_extras(
        "frontier", {"frontier": {"pov_claims": []}}, "assimilation") == []
    # a key that is legal in ONE direction is refused in the other
    req_err = U.validate_extras("frontier", {"frontier": {"pov_claims": []}})
    assert any("unknown extras.frontier key" in e for e in req_err), req_err
    assim_err = U.validate_extras(
        "frontier", {"frontier": {"paths": []}}, "assimilation")
    assert any("unknown extras.frontier key" in e for e in assim_err), assim_err


def test_unknown_direction_is_refused():
    errors = U.validate_extras("frontier", {"frontier": {"paths": []}}, "sideways")
    assert errors == ["unknown direction: 'sideways'"]


def test_request_builder_still_refuses_unknown_lane_and_key():
    assert U.build_request(lane="midway") == {}
    assert U.build_request(lane="frontier",
                           extras={"frontier": {"bogus": 1}}) == {}


# --- 3. a NEW lane: one table entry, no builder edit ----------------------

def _fake_lane(monkeypatch):
    monkeypatch.setitem(R.LANES, "midway", {
        "request": frozenset({"alpha"}),
        "assimilation": frozenset({"beta"}),
    })
    return "midway"


def test_new_fake_lane_registers_and_builds_without_touching_builders(monkeypatch):
    lane = _fake_lane(monkeypatch)

    # the builders see the lane through the registry alone
    assert lane in U.LANES
    assert U.EXTRAS_KEYS[lane] == frozenset({"alpha"})
    assert U.ASSIM_EXTRAS_KEYS[lane] == frozenset({"beta"})

    request = U.build_request(
        lane=lane, trigger="pre", session_id="s-fake",
        agent_card={"persona": "P"}, ask={"text": "q", "kind": "user"},
        context={}, stance={}, extras={lane: {"alpha": "A"}})
    assert request["schema"] == "lane-request/1"
    assert request["lane"] == lane
    assert request["extras"][lane] == {"alpha": "A"}
    assert U.validate_request(request) == []

    # ... and the OTHER direction builds from the same table entry (U2)
    assimilation = U.build_assimilation(
        lane=lane, producer="p-fake", answer="built", kind="fake_plan",
        extras={lane: {"beta": "B"}})
    assert assimilation["schema"] == "lane-assimilation/1"
    assert assimilation["lane"] == lane
    assert assimilation["extras"][lane] == {"beta": "B"}
    assert U.validate_assimilation(assimilation) == []

    # the same lane's OTHER closed set is what the other direction allows
    assert U.validate_extras(lane, {lane: {"beta": "B"}}, "assimilation") == []
    assert U.validate_extras(lane, {lane: {"alpha": "A"}}, "assimilation")

    # refusal still holds for the fake lane's unknown key, in both directions
    assert U.build_request(lane=lane, extras={lane: {"bogus": 1}}) == {}
    assert U.build_assimilation(lane=lane, extras={lane: {"bogus": 1}}) == {}
