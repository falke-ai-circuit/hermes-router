"""M2.2 pins: lane OWNERSHIP of the unified envelope, all four seams.

A lane may only ever build its OWN envelope, in BOTH directions. The pins:

  1. decision PRE + MIDTURN requests carry lane="decision" and validate;
  2. frontier PRE request + POST assimilation carry lane="frontier";
  3. a lane cannot STEAL another lane's envelope — a foreign extras block or a
     foreign extras key is refused ({}), never silently relabelled;
  4. no lane's closed extras key set is a subset of another's, so a stolen key
     can never validate for both.
"""
from __future__ import annotations

import pytest

from hermes_router import decision as D
from hermes_router import router_core as RC
from hermes_router.features.envelope import registry as R
from hermes_router.features.envelope import unified as U

LANE_EXTRA_PROBES = {
    "decision": {"options": [{"id": "opt-1"}], "chosen": "opt-1"},
    "frontier": {"paths": ["explorer"], "consult_kind": "consultation"},
    "uncensored": {"leg": "post", "render": "body", "acceptance": "accepted"},
}


# --- 1/2. every seam inherits its own lane's template ----------------------

def test_decision_pre_and_midturn_requests_carry_the_decision_lane():
    for trigger in ("pre", "midturn"):
        env = D.build_envelope("s-own-%s" % trigger, "redis or memcached?",
                               ["redis", "memcached"], trigger)
        assert env, "decision %s produced no envelope" % trigger
        assert env["schema"] == D.ENVELOPE_SCHEMA
        assert set(D.build_decision_assimilation(env, None)) >= {"schema", "lane"}


def test_frontier_pre_request_carries_the_frontier_lane():
    env = RC.build_frontier_request("weigh the tradeoffs", agent="coder",
                                    role="engineer", route_id="r-1",
                                    consult_kind="consultation")
    assert env, "frontier PRE request is empty"
    assert env["lane"] == "frontier"
    assert env["schema"] == U.SCHEMA_REQUEST
    assert U.validate_request(env) == [], U.validate_request(env)
    assert env["extras"]["frontier"]["paths"] == [p.name for p in
                                                  __import__("hermes_router.povs",
                                                             fromlist=["POV_SET"]).POV_SET]


def test_frontier_post_assimilation_carries_the_frontier_lane():
    class _D:
        route_id = "r-2"
        task_id = "t-2"

    env = RC.build_frontier_envelope("consultation", "frontier", _D(),
                                     "the answer", evidence_refs=["a.md"],
                                     consult_kind="consultation")
    assert env, "frontier POST assimilation is empty"
    assert env["lane"] == "frontier"
    assert env["schema"] == U.SCHEMA_ASSIMILATION
    assert U.validate_assimilation(env) == [], U.validate_assimilation(env)


# --- 3. a lane cannot steal another lane's envelope ------------------------

@pytest.mark.parametrize("lane", sorted(R.LANES))
def test_foreign_extras_block_is_refused(lane):
    for other in R.LANES:
        if other == lane:
            continue
        assert U.build_request(lane=lane,
                               extras={other: dict(LANE_EXTRA_PROBES[other])}) == {}
        assert U.build_assimilation(lane=lane,
                                    extras={other: dict(LANE_EXTRA_PROBES[other])}) == {}


@pytest.mark.parametrize("lane", sorted(R.LANES))
def test_foreign_extras_key_is_refused(lane):
    for other in R.LANES:
        if other == lane:
            continue
        for direction, builder, keyset in (
                ("request", U.build_request, R.EXTRAS_KEYS),
                ("assimilation", U.build_assimilation, R.ASSIM_EXTRAS_KEYS)):
            for key in sorted(keyset[other] - keyset[lane]):
                assert builder(lane=lane, extras={lane: {key: None}}) == {}, (
                    lane, direction, key)


def test_no_lane_key_set_is_shared():
    for direction, keyset in (("request", R.EXTRAS_KEYS),
                              ("assimilation", R.ASSIM_EXTRAS_KEYS)):
        for lane in R.LANES:
            for other in R.LANES:
                if lane == other:
                    continue
                assert keyset[lane] and not (keyset[lane] <= keyset[other]), (
                    direction, lane, other)


def test_a_built_envelope_never_carries_another_lane_block():
    for lane in sorted(R.LANES):
        request = U.build_request(lane=lane, extras={lane: {"leg": "pre"}
                                                    if lane == "uncensored"
                                                    else {}})
        assert request, lane
        assert set(request["extras"]) == {lane}
        assim = U.build_assimilation(
            lane=lane,
            extras={lane: {k: "probe" for k in
                           R.required_keys(lane, "assimilation")}})
        assert assim and set(assim["extras"]) == {lane}
